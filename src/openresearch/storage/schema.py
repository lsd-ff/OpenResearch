from sqlalchemy import JSON, Column, ForeignKey, Index, Integer, MetaData, String, Table

metadata = MetaData()

uploads = Table(
    "uploads",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("payload", JSON, nullable=False),
)
Index("ix_uploads_owner", uploads.c.owner_id)

conversations = Table(
    "conversations",
    metadata,
    Column("owner_id", String, primary_key=True),
    Column("session_id", String, primary_key=True),
    Column("payload", JSON, nullable=False),
)

plans = Table(
    "plans",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("payload", JSON, nullable=False),
)
Index("ix_plans_status", plans.c.status)

attempts = Table(
    "task_attempts",
    metadata,
    Column("id", String, primary_key=True),
    Column("plan_id", String, ForeignKey("plans.id"), nullable=False),
    Column("task_id", String, nullable=False),
    Column("worker_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("finished_at", String),
    Column("error", String, nullable=False, default=""),
)

events = Table(
    "events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("plan_id", String, ForeignKey("plans.id"), nullable=False),
    Column("payload", JSON, nullable=False),
)
Index("ix_events_plan_sequence", events.c.plan_id, events.c.sequence)

artifacts = Table(
    "artifacts",
    metadata,
    Column("id", String, primary_key=True),
    Column("plan_id", String, ForeignKey("plans.id"), nullable=False),
    Column("execution_id", String, ForeignKey("task_attempts.id"), nullable=False),
    Column("payload", JSON, nullable=False),
)

sandboxes = Table(
    "sandboxes",
    metadata,
    Column("id", String, primary_key=True),
    Column("plan_id", String, ForeignKey("plans.id"), nullable=False),
    Column("execution_id", String, ForeignKey("task_attempts.id"), nullable=False),
    Column("worker_id", String, nullable=False),
    Column("backend", String, nullable=False),
    Column("status", String, nullable=False),
    Column("error", String, nullable=False, default=""),
)
