from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, insert, select, update
from sqlalchemy.engine import Connection

from openresearch.errors import NotFoundError
from openresearch.models.runtime import (
    Event,
    Plan,
    Task,
    utcnow,
)
from openresearch.storage.schema import events, plans


class Database:
    def __init__(self, database_url: str) -> None:
        if not database_url.startswith("sqlite:///"):
            raise ValueError("this runtime currently supports local SQLite only")
        database_path = Path(database_url.removeprefix("sqlite:///"))
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(database_url, connect_args={"timeout": 15})

        @event.listens_for(self.engine, "connect")
        def configure_sqlite(dbapi_connection: Any, _: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.commit()
        self.upgrade()

    def upgrade(self) -> None:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        # 迁移与业务写入使用同一把锁，防止 API 和 Worker 首次启动同时建表。
        with self.transaction() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def close(self) -> None:
        self.engine.dispose()

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self.engine.connect() as connection:
            # 先拿写锁再读取状态，避免两个进程同时决定领取同一个节点。
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def load(self, connection: Connection, plan_id: str, owner_id: str | None = None) -> Plan:
        row = connection.execute(select(plans.c.payload).where(plans.c.id == plan_id)).first()
        if row is None:
            raise NotFoundError("plan not found")
        plan = Plan.model_validate(row[0])
        if owner_id is not None and plan.owner_id != owner_id:
            raise NotFoundError("plan not found")
        return plan

    def save(self, connection: Connection, plan: Plan) -> None:
        plan.updated_at = utcnow()
        connection.execute(
            update(plans)
            .where(plans.c.id == plan.id)
            .values(status=plan.status.value, payload=plan.model_dump(mode="json", by_alias=True))
        )

    def publish(
        self,
        connection: Connection,
        plan: Plan,
        kind: str,
        task: Task | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        value = Event(
            plan_id=plan.id,
            event_type=kind,
            trace_id=plan.trace_id,
            task_id=task.id if task else None,
            task_status=task.status if task else plan.status,
            execution_id=task.execution_id if task else None,
            span_id=f"task_{task.id}" if task else None,
            payload=payload or {},
        )
        connection.execute(
            insert(events).values(plan_id=plan.id, payload=value.model_dump(mode="json"))
        )
