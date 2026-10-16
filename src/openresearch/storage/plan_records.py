from sqlalchemy import insert
from sqlalchemy.engine import Connection

from openresearch.models.runtime import Plan
from openresearch.storage.database import Database
from openresearch.storage.schema import plans


def insert_plan(database: Database, connection: Connection, plan: Plan) -> None:
    connection.execute(
        insert(plans).values(
            id=plan.id,
            owner_id=plan.owner_id,
            status=plan.status.value,
            payload=plan.model_dump(mode="json", by_alias=True),
        )
    )
    database.publish(connection, plan, "plan_created")
