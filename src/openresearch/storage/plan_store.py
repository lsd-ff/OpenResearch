from openresearch.errors import ConflictError, NotFoundError
from openresearch.models.runtime import (
    Plan,
    Status,
    utcnow,
)
from openresearch.storage.conversations import ConversationStore
from openresearch.storage.database import Database
from openresearch.storage.plan_records import insert_plan
from openresearch.storage.transitions import Transitions

TERMINAL = {Status.COMPLETED, Status.FAILED, Status.CANCELED}


class PlanStore:
    def __init__(self, database: Database, transitions: Transitions) -> None:
        self.database = database
        self.transitions = transitions
        self.sessions = ConversationStore(database)

    def save_plan(self, plan: Plan) -> None:
        with self.database.transaction() as connection:
            insert_plan(self.database, connection, plan)
            self.sessions.link_plan(connection, plan)

    def get_plan(self, plan_id: str, owner_id: str | None = None) -> Plan:
        with self.database.engine.connect() as connection:
            return self.database.load(connection, plan_id, owner_id)

    def queue_plan(self, plan_id: str, owner_id: str) -> None:
        with self.database.transaction() as connection:
            plan = self.database.load(connection, plan_id, owner_id)
            if plan.status != Status.PENDING:
                raise ConflictError("only pending plans can be queued")
            if plan.approval.required and plan.approval.status != "approved":
                raise ConflictError("plan requires approval")
            plan.status = Status.READY
            plan.execution_scope = None
            plan.usage.started_at = utcnow()
            plan.usage.finished_at = None
            self.database.save(connection, plan)
            self.database.publish(connection, plan, "plan_queued")

    def approve(self, plan_id: str, owner_id: str) -> None:
        with self.database.transaction() as connection:
            plan = self.database.load(connection, plan_id, owner_id)
            if plan.status != Status.AWAITING_APPROVAL:
                raise ConflictError("plan is not awaiting approval")
            plan.approval.status = "approved"
            plan.approval.approved_by = owner_id
            plan.approval.approved_at = utcnow()
            plan.status = Status.PENDING
            self.database.save(connection, plan)
            self.database.publish(connection, plan, "plan_approved")

    def cancel(self, plan_id: str, owner_id: str) -> None:
        with self.database.transaction() as connection:
            plan = self.database.load(connection, plan_id, owner_id)
            if plan.status in TERMINAL:
                raise ConflictError("plan has already terminated")
            self.transitions.cancel(connection, plan, "canceled by user")

    def retry(self, plan_id: str, task_id: str, owner_id: str) -> None:
        with self.database.transaction() as connection:
            plan = self.database.load(connection, plan_id, owner_id)
            if plan.status not in {Status.FAILED, Status.CANCELED}:
                raise ConflictError("retry requires a failed or canceled plan")
            node = next((node for node in plan.nodes if node.id == task_id), None)
            if node is None:
                raise NotFoundError("task not found")
            if node.status not in {Status.FAILED, Status.BLOCKED, Status.CANCELED}:
                raise ConflictError("only failed, blocked or canceled tasks can be retried")
            affected = {task_id}
            while True:
                extra = {
                    task.id for task in plan.nodes if set(task.dependencies) & affected
                } - affected
                if not extra:
                    break
                affected |= extra
            for task in plan.nodes:
                if task.id not in affected:
                    continue
                task.status = Status.PENDING
                task.result = task.code = task.structured_data = task.error = ""
                Transitions.invalidate(task)
                for key in task.output_artifacts:
                    plan.artifacts.pop(key, None)
            plan.status = Status.PENDING
            plan.usage.finished_at = None
            self.database.save(connection, plan)
            self.database.publish(connection, plan, "task_retry_requested", node)
