from sqlalchemy import update
from sqlalchemy.engine import Connection

from openresearch.models.runtime import (
    Plan,
    Status,
    Task,
    utcnow,
)
from openresearch.storage.database import Database
from openresearch.storage.schema import attempts

ACTIVE = {Status.PENDING, Status.READY, Status.IN_PROGRESS}


class Transitions:
    def __init__(self, database: Database) -> None:
        self.database = database

    def cancel(self, connection: Connection, plan: Plan, reason: str) -> None:
        plan.status = Status.CANCELED
        plan.usage.finished_at = utcnow()
        for node in plan.nodes:
            if node.status in ACTIVE or node.status == Status.BLOCKED:
                if node.execution_id:
                    connection.execute(
                        update(attempts)
                        .where(attempts.c.id == node.execution_id)
                        .values(status="canceled", finished_at=utcnow().isoformat(), error=reason)
                    )
                node.status = Status.CANCELED
                node.error = reason
                self.database.publish(connection, plan, "task_canceled", node)
                self.invalidate(node)
        self.database.save(connection, plan)
        self.database.publish(connection, plan, "plan_canceled", payload={"reason": reason})

    @staticmethod
    def invalidate(node: Task) -> None:
        node.execution_id = None
        node.lease_owner = None
        node.lease_expires_at = None

    def refresh(self, connection: Connection, plan: Plan) -> None:
        by_id = {node.id: node for node in plan.nodes}
        changed = True
        while changed:
            changed = False
            for node in plan.nodes:
                if node.status not in {Status.PENDING, Status.READY}:
                    continue
                if any(
                    by_id[dep].status in {Status.FAILED, Status.BLOCKED, Status.CANCELED}
                    for dep in node.dependencies
                ):
                    node.status = Status.BLOCKED
                    node.error = "upstream task did not complete"
                    self.database.publish(connection, plan, "task_blocked", node)
                    changed = True
                elif (
                    node.status == Status.PENDING
                    and all(by_id[dep].status == Status.COMPLETED for dep in node.dependencies)
                    and all(key in plan.artifacts for key in node.required_artifacts)
                ):
                    node.status = Status.READY
                    self.database.publish(connection, plan, "task_ready", node)
                    changed = True
        scope = [
            node
            for node in plan.nodes
            if plan.execution_scope is None or node.id in plan.execution_scope
        ]
        if plan.execution_scope is not None and not any(node.status in ACTIVE for node in scope):
            plan.status = (
                Status.FAILED
                if any(node.status != Status.COMPLETED for node in scope)
                else Status.PENDING
            )
            plan.execution_scope = None
            self.database.publish(connection, plan, "task_scope_completed")
        if not any(node.status in ACTIVE for node in plan.nodes):
            plan.status = (
                Status.COMPLETED
                if all(node.status == Status.COMPLETED for node in plan.nodes)
                else Status.FAILED
            )
            plan.usage.finished_at = utcnow()
            self.database.publish(connection, plan, f"plan_{plan.status}")
