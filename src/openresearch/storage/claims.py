from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from openresearch.models.runtime import Plan, Status, Task, new_id, utcnow
from openresearch.storage.database import Database
from openresearch.storage.leases import duration_exhausted
from openresearch.storage.schema import attempts, plans
from openresearch.storage.transitions import Transitions


@dataclass(frozen=True)
class ClaimPolicy:
    worker_id: str
    lease_seconds: int
    max_concurrent: int


def eligible_task(plan: Plan) -> Task | None:
    running = [node for node in plan.nodes if node.status == Status.IN_PROGRESS]
    if any(not node.parallelizable for node in running):
        return None
    ready = sorted(
        (
            node
            for node in plan.nodes
            if node.status == Status.READY
            and (plan.execution_scope is None or node.id in plan.execution_scope)
        ),
        key=lambda node: (-node.priority, node.id),
    )
    return next((node for node in ready if node.parallelizable or not running), None)


class TaskClaims:
    def __init__(self, database: Database, transitions: Transitions) -> None:
        self.database = database
        self.transitions = transitions

    def claim_next(
        self, worker_id: str, lease_seconds: int, max_concurrent: int
    ) -> tuple[Plan, Task] | None:
        """在 SQLite 写锁内同时检查全局配额并领取节点，多进程不会重复领取。"""
        policy = ClaimPolicy(worker_id, lease_seconds, max_concurrent)
        with self.database.transaction() as connection:
            active = self._active_plans(connection)
            self._expire_budgets(connection, active)
            running_count = sum(
                node.status == Status.IN_PROGRESS for plan in active for node in plan.nodes
            )
            if running_count >= policy.max_concurrent:
                return None
            for plan in active:
                if plan.status == Status.CANCELED:
                    continue
                self.transitions.refresh(connection, plan)
                node = eligible_task(plan)
                if node is None:
                    self.database.save(connection, plan)
                    continue
                if plan.usage.task_attempts >= plan.budget.max_task_attempts:
                    self.transitions.cancel(connection, plan, "task attempt budget exhausted")
                    continue
                self._start(connection, plan, node, policy)
                return plan, node
        return None

    def _active_plans(self, connection: Connection) -> list[Plan]:
        rows = connection.execute(
            select(plans.c.payload).where(
                plans.c.status.in_([Status.READY.value, Status.IN_PROGRESS.value])
            )
        ).all()
        return [Plan.model_validate(row[0]) for row in rows]

    def _expire_budgets(self, connection: Connection, active: list[Plan]) -> None:
        for plan in active:
            if duration_exhausted(plan):
                self.transitions.cancel(connection, plan, "plan duration budget exhausted")

    def _start(self, connection: Connection, plan: Plan, node: Task, policy: ClaimPolicy) -> None:
        if plan.status == Status.READY:
            plan.status = Status.IN_PROGRESS
            self.database.publish(connection, plan, "plan_started")
        node.status = Status.IN_PROGRESS
        node.execution_id = new_id()
        node.execution_epoch += 1
        node.run_count += 1
        node.lease_owner = policy.worker_id
        node.lease_expires_at = utcnow() + timedelta(seconds=policy.lease_seconds)
        plan.usage.task_attempts += 1
        connection.execute(
            insert(attempts).values(
                id=node.execution_id,
                plan_id=plan.id,
                task_id=node.id,
                worker_id=policy.worker_id,
                epoch=node.execution_epoch,
                status="in_progress",
                started_at=utcnow().isoformat(),
                error="",
            )
        )
        self.database.save(connection, plan)
        self.database.publish(connection, plan, "task_started", node)
