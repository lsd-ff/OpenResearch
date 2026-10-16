from alembic import context
from sqlalchemy.engine import Connection

from openresearch.storage.schema import metadata

connection = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError("run migrations through 'openresearch db upgrade'")
context.configure(connection=connection, target_metadata=metadata, transaction_per_migration=True)
with context.begin_transaction():
    context.run_migrations()
