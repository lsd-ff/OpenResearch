from sqlalchemy import func, select, true

from openresearch.errors import ConflictError, NotFoundError
from openresearch.models.graph import ancestor_map
from openresearch.models.routing import validate_role
from openresearch.models.runtime import Plan, Status, Task, utcnow
from openresearch.storage.database import Database
from openresearch.storage.schema import plans


def find_node(plan: Plan, task_id: str) -> Task:
    node = next((node for node in plan.nodes if node.id == task_id), None)
    if node is None:
        raise NotFoundError("task not found")
    return node


class TaskCommands:
    def __init__(self, database: Database) -> None:
        self.database = database

    def find_plan(self, task_id: str, owner_id: str) -> str:
        nodes = func.json_each(plans.c.payload["nodes"]).table_valued("value")
        with self.database.engine.connect() as connection:
            identifiers: list[str] = list(
                connection.execute(
                    select(plans.c.id)
                    .join(nodes, true())
                    .where(
                        plans.c.owner_id == owner_id,
                        func.json_extract(nodes.c.value, "$.id") == task_id,
                    )
                ).scalars()
            )
        if len(identifiers) != 1:
            raise NotFoundError("task not found")
        return str(identifiers[0])

    def queue(self, plan_id: str, task_id: str, owner_id: str) -> None:
        with self.database.transaction() as connection:
            plan = self.database.load(connection, plan_id, owner_id)
            node = find_node(plan, task_id)
            if plan.status != Status.PENDING or node.status not in {Status.PENDING, Status.READY}:
                raise ConflictError("single task execution requires a pending plan and task")
            if plan.approval.required and plan.approval.status != "approved":
                raise ConflictError("plan requires approval")
            if any(
                find_node(plan, parent).status != Status.COMPLETED for parent in node.dependencies
            ):
                raise ConflictError("task dependencies must complete before direct execution")
            if any(key not in plan.artifacts for key in node.required_artifacts):
                raise ConflictError("task is missing its required artifacts")
            plan.execution_scope = [task_id]
            plan.status = Status.READY
            plan.usage.started_at = plan.usage.started_at or utcnow()
            plan.usage.finished_at = None
            self.database.save(connection, plan)
            self.database.publish(connection, plan, "task_queued", node)

    def reassign(self, plan_id: str, task_id: str, role: str, owner_id: str) -> None:
        with self.database.transaction() as connection:
            plan = self.database.load(connection, plan_id, owner_id)
            node = find_node(plan, task_id)
            if plan.status != Status.PENDING or node.status not in {Status.PENDING, Status.READY}:
                raise ConflictError("only unstarted tasks in pending plans can be reassigned")
            validate_role(node.type, role)
            node.assigned_to = role
            self.database.save(connection, plan)
            self.database.publish(connection, plan, "task_reassigned", node, {"assigned_to": role})

    def queue_stage(self, plan_id: str, task_id: str, owner_id: str) -> None:
        """在同一写事务中限定阶段及其祖先；保留整图、已有结果和累计预算。"""
        with self.database.transaction() as connection:
            plan = self.database.load(connection, plan_id, owner_id)
            node = find_node(plan, task_id)
            if node.status in {Status.COMPLETED, Status.FAILED, Status.BLOCKED, Status.CANCELED}:
                return
            if plan.approval.required and plan.approval.status != "approved":
                raise ConflictError("plan requires approval")
            if plan.status in {Status.READY, Status.IN_PROGRESS}:
                if plan.execution_scope is None or task_id in plan.execution_scope:
                    return
                raise ConflictError("another stage is already executing in this plan")
            # 阶段失败后仍可生成既定只读总报告；失败节点不重排，原预算和错误继续保留。
            finalizing = (
                plan.status == Status.FAILED
                and node.type == "framework_report"
                and node.trigger_rule == "all_done"
                and node.contract.allowed_tools == ["comparison.report"]
            )
            if plan.status != Status.PENDING and not finalizing:
                raise ConflictError(
                    "stage execution requires a pending plan; retry failures explicitly"
                )
            ancestors = ancestor_map({task.id: task for task in plan.nodes})[task_id]
            scope = ancestors | {task_id}
            plan.execution_scope = [task.id for task in plan.nodes if task.id in scope]
            plan.status = Status.READY
            plan.usage.started_at = plan.usage.started_at or utcnow()
            plan.usage.finished_at = None
            self.database.save(connection, plan)
            self.database.publish(
                connection, plan, "stage_queued", node, {"execution_scope": plan.execution_scope}
            )
