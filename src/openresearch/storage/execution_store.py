from datetime import timedelta

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from openresearch.errors import ContractError, StaleExecutionError
from openresearch.models.execution import ExecutionLease, TaskCompletion, validate_output_keys
from openresearch.models.runtime import Plan, Status, Task, utcnow
from openresearch.storage.conversations import ConversationStore
from openresearch.storage.database import Database
from openresearch.storage.leases import current_task, duration_exhausted
from openresearch.storage.schema import artifacts, attempts, plans
from openresearch.storage.transitions import Transitions


class ExecutionStore:
    def __init__(self, database: Database, transitions: Transitions) -> None:
        self.database = database
        self.transitions = transitions
        self.sessions = ConversationStore(database)

    def renew(self, lease: ExecutionLease, lease_seconds: int) -> bool:
        """续约失败表示任务已失效，Worker 应取消正在运行的执行。"""
        with self.database.transaction() as connection:
            plan = self.database.load(connection, lease.plan_id)
            try:
                node = current_task(plan, lease)
            except StaleExecutionError:
                return False
            if duration_exhausted(plan):
                self.transitions.cancel(connection, plan, "plan duration budget exhausted")
                return False
            node.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
            self.database.save(connection, plan)
            return True

    def execution_committed(self, lease: ExecutionLease) -> bool:
        """只有此身份提交的结果终态才算完成；取消、失联或重分配仍须停止执行。"""
        with self.database.engine.connect() as connection:
            status = connection.execute(
                select(attempts.c.status).where(
                    attempts.c.id == lease.execution_id,
                    attempts.c.plan_id == lease.plan_id,
                    attempts.c.task_id == lease.task_id,
                    attempts.c.worker_id == lease.worker_id,
                )
            ).scalar_one_or_none()
            return status in {"completed", "failed"}

    def finish(self, lease: ExecutionLease, completion: TaskCompletion) -> None:
        """结果、产物索引、事件、后继节点状态在同一事务中提交。"""
        with self.database.transaction() as connection:
            plan = self.database.load(connection, lease.plan_id)
            node = current_task(plan, lease)
            expired = duration_exhausted(plan)
            if expired:
                self.transitions.cancel(connection, plan, "plan duration budget exhausted")
            else:
                self._finish_current(connection, plan, node, completion)
        if expired:
            # 必须先提交取消状态，再抛异常；在事务内抛异常会回滚取消操作。
            raise StaleExecutionError("plan duration budget exhausted")

    def _finish_current(
        self, connection: Connection, plan: Plan, node: Task, completion: TaskCompletion
    ) -> None:
        succeeded = completion.result is not None and completion.result.success
        if succeeded:
            self._complete(connection, plan, node, completion)
        else:
            if completion.result is not None:
                # 实验失败也保存真实输出和审计证据，不能只留下字符串错误。
                self._publish_artifacts(connection, plan, node, completion)
                node.result = completion.result.result
                node.code = completion.result.code
                node.structured_data = completion.result.structured_data
                node.image_base64 = completion.result.image_base64
            error = completion.result.error if completion.result else completion.error
            self._fail(connection, plan, node, error)
        connection.execute(
            update(attempts)
            .where(attempts.c.id == node.execution_id)
            .values(
                status="completed" if succeeded else "failed",
                finished_at=utcnow().isoformat(),
                error=node.error,
            )
        )
        Transitions.invalidate(node)
        self.transitions.refresh(connection, plan)
        self.database.save(connection, plan)

    def _complete(
        self, connection: Connection, plan: Plan, node: Task, completion: TaskCompletion
    ) -> None:
        result = completion.result
        if result is None:
            raise ContractError("successful completion requires a result")
        self._publish_artifacts(connection, plan, node, completion)
        if "parsed_paper" in node.output_artifacts:
            self.sessions.link_plan(connection, plan, document=True)
        node.status = Status.COMPLETED
        node.error = ""
        node.result, node.code, node.structured_data = (
            result.result,
            result.code,
            result.structured_data,
        )
        node.image_base64 = result.image_base64
        self.database.publish(
            connection,
            plan,
            "task_completed",
            node,
            {
                "result": node.result,
                "code": node.code,
                "structured_data": node.structured_data,
                "image_base64": node.image_base64,
            },
        )

    def _publish_artifacts(
        self, connection: Connection, plan: Plan, node: Task, completion: TaskCompletion
    ) -> None:
        result = completion.result
        keys = [item.key for item in completion.artifacts]
        validate_output_keys(
            node.output_artifacts,
            keys,
            result is not None and result.success,
            node.contract.optional_output_artifacts,
        )
        if result is not None and set(keys) != set(result.outputs):
            raise ContractError("published artifacts must match the returned output keys")
        for item in completion.artifacts:
            if item.execution_id != node.execution_id or item.producer_task_id != node.id:
                raise ContractError("artifact belongs to a different execution")
            connection.execute(
                insert(artifacts).values(
                    id=item.id,
                    plan_id=plan.id,
                    execution_id=node.execution_id,
                    payload=item.model_dump(mode="json"),
                )
            )
            plan.artifacts[item.key] = item
            self.database.publish(
                connection,
                plan,
                "artifact_created",
                node,
                {
                    "key": item.key,
                    "id": item.id,
                    "sha256": item.sha256,
                },
            )

    def _fail(self, connection: Connection, plan: Plan, node: Task, error: str) -> None:
        node.error = error[:4000] or "task failed"
        node.status = Status.PENDING if node.run_count <= node.retry_limit else Status.FAILED
        kind = "task_retrying" if node.status == Status.PENDING else "task_failed"
        self.database.publish(connection, plan, kind, node, {"error": node.error})

    def append_log(self, lease: ExecutionLease, message: str, stream: str = "stdout") -> None:
        with self.database.transaction() as connection:
            plan = self.database.load(connection, lease.plan_id)
            node = current_task(plan, lease)
            self.database.publish(
                connection,
                plan,
                "task_log",
                node,
                {
                    "message": message[:8192],
                    "stream": stream,
                },
            )

    def recover_expired(self) -> int:
        """只恢复过期租约；存活 Worker 的任务不会被另一个 Worker 抢走。"""
        recovered = 0
        with self.database.transaction() as connection:
            rows = connection.execute(
                select(plans.c.payload).where(plans.c.status == Status.IN_PROGRESS.value)
            ).all()
            for row in rows:
                plan = Plan.model_validate(row[0])
                expired = [
                    node
                    for node in plan.nodes
                    if (
                        node.status == Status.IN_PROGRESS
                        and node.lease_expires_at is not None
                        and node.lease_expires_at <= utcnow()
                    )
                ]
                for node in expired:
                    self._recover(connection, plan, node)
                if expired:
                    self.database.save(connection, plan)
                recovered += len(expired)
        return recovered

    def _recover(self, connection: Connection, plan: Plan, node: Task) -> None:
        connection.execute(
            update(attempts)
            .where(attempts.c.id == node.execution_id)
            .values(status="lost", finished_at=utcnow().isoformat(), error="lease expired")
        )
        node.status = Status.PENDING
        self.database.publish(connection, plan, "task_recovered", node)
        Transitions.invalidate(node)
