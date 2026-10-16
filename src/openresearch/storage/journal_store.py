from sqlalchemy import select

from openresearch.errors import NotFoundError
from openresearch.models.runtime import (
    Artifact,
    Event,
)
from openresearch.storage.database import Database
from openresearch.storage.schema import artifacts, events


class JournalStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_events(self, plan_id: str, after: int = 0, limit: int = 200) -> list[Event]:
        with self.database.engine.connect() as connection:
            rows = connection.execute(
                select(events.c.sequence, events.c.payload)
                .where(events.c.plan_id == plan_id, events.c.sequence > after)
                .order_by(events.c.sequence)
                .limit(limit)
            ).all()
            return [Event.model_validate({**row[1], "sequence": row[0]}) for row in rows]

    def get_artifact(self, plan_id: str, artifact_id: str, owner_id: str) -> Artifact:
        with self.database.engine.connect() as connection:
            self.database.load(connection, plan_id, owner_id)
            row = connection.execute(
                select(artifacts.c.payload).where(
                    artifacts.c.id == artifact_id, artifacts.c.plan_id == plan_id
                )
            ).first()
            if row is None:
                raise NotFoundError("artifact not found")
            return Artifact.model_validate(row[0])
