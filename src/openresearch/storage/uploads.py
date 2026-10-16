from sqlalchemy import insert, select

from openresearch.errors import NotFoundError
from openresearch.models.context import Identity
from openresearch.models.documents import UploadRecord
from openresearch.storage.conversations import ConversationStore
from openresearch.storage.database import Database
from openresearch.storage.schema import uploads


class UploadStore:
    def __init__(self, database: Database, sessions: ConversationStore) -> None:
        self.database = database
        self.sessions = sessions

    def save(self, record: UploadRecord) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                insert(uploads).values(
                    id=record.id,
                    owner_id=record.owner_id,
                    session_id=record.session_id,
                    payload=record.model_dump(mode="json"),
                )
            )
            self.sessions.link_upload(
                connection, Identity(record.owner_id, record.session_id), record.id
            )

    def get(self, upload_id: str, owner_id: str) -> UploadRecord:
        with self.database.engine.connect() as connection:
            raw = connection.execute(
                select(uploads.c.payload).where(
                    uploads.c.id == upload_id,
                    uploads.c.owner_id == owner_id,
                )
            ).first()
            if raw is None:
                raise NotFoundError("upload not found")
            return UploadRecord.model_validate(raw[0])
