from sqlalchemy import insert, select, update

from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import Status, utcnow
from openresearch.storage.database import Database
from openresearch.storage.leases import current_task
from openresearch.storage.schema import attempts, sandboxes


class SandboxStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def register_sandbox(self, lease: ExecutionLease, sandbox_id: str, backend: str) -> None:
        with self.database.transaction() as connection:
            plan = self.database.load(connection, lease.plan_id)
            current_task(plan, lease)
            connection.execute(
                insert(sandboxes).values(
                    id=sandbox_id,
                    plan_id=lease.plan_id,
                    execution_id=lease.execution_id,
                    worker_id=lease.worker_id,
                    backend=backend,
                    status="active",
                    error="",
                )
            )

    def mark_sandbox_closed(self, sandbox_id: str, error: str = "") -> None:
        with self.database.transaction() as connection:
            connection.execute(
                update(sandboxes)
                .where(sandboxes.c.id == sandbox_id)
                .values(status="cleanup_pending" if error else "closed", error=error[:4000])
            )

    def cleanup_candidates(self, backend: str) -> list[str]:
        with self.database.engine.connect() as connection:
            query = (
                select(sandboxes.c.id)
                .join(attempts, attempts.c.id == sandboxes.c.execution_id)
                .where(
                    sandboxes.c.backend == backend,
                    sandboxes.c.status != "closed",
                    (sandboxes.c.status == "cleanup_pending")
                    | (attempts.c.status != "in_progress"),
                )
            )
            return list(connection.execute(query).scalars())

    def execution_live(self, execution_id: str) -> bool:
        with self.database.engine.connect() as connection:
            attempt = (
                connection.execute(select(attempts).where(attempts.c.id == execution_id))
                .mappings()
                .one_or_none()
            )
            if attempt is None or attempt["status"] != "in_progress":
                return False
            plan = self.database.load(connection, attempt["plan_id"])
            return any(
                node.execution_id == execution_id
                and node.status == Status.IN_PROGRESS
                and node.lease_expires_at is not None
                and node.lease_expires_at > utcnow()
                for node in plan.nodes
            )
