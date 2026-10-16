from dataclasses import dataclass

from sqlalchemy import ColumnElement, insert, select, update
from sqlalchemy.engine import Connection

from openresearch.errors import ConflictError
from openresearch.models.context import Conversation, ConversationTurn, Identity
from openresearch.models.planning import PendingPlanning
from openresearch.models.runtime import Plan, Status, utcnow
from openresearch.storage.database import Database
from openresearch.storage.plan_records import insert_plan
from openresearch.storage.schema import conversations


@dataclass(frozen=True)
class ConversationUpdate:
    snapshot: Conversation
    user: ConversationTurn
    assistant: ConversationTurn
    pending: PendingPlanning | None
    plan: Plan | None = None


class ConversationStore:
    """有界服务端上下文；写事务同时保护修订号与消息顺序。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _query(identity: Identity) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
        return (
            conversations.c.owner_id == identity.user_id,
            conversations.c.session_id == identity.session_id,
        )

    def load(self, connection: Connection, identity: Identity) -> Conversation:
        raw = connection.execute(
            select(conversations.c.payload).where(*self._query(identity))
        ).first()
        if raw is None:
            return Conversation(owner_id=identity.user_id, session_id=identity.session_id)
        return Conversation.model_validate(raw[0])

    def get(self, identity: Identity) -> Conversation:
        with self.database.engine.connect() as connection:
            return self.load(connection, identity)

    def save(self, connection: Connection, context: Conversation) -> None:
        identity = Identity(context.owner_id, context.session_id)
        exists = connection.execute(
            select(conversations.c.owner_id).where(*self._query(identity))
        ).first()
        context.revision += 1
        context.updated_at = utcnow()
        payload = context.model_dump(mode="json")
        if exists:
            connection.execute(
                update(conversations).where(*self._query(identity)).values(payload=payload)
            )
            return
        connection.execute(
            insert(conversations).values(
                owner_id=context.owner_id,
                session_id=context.session_id,
                payload=payload,
            )
        )

    def link_upload(self, connection: Connection, identity: Identity, upload_id: str) -> None:
        context = self.load(connection, identity)
        context.uploads = [*context.uploads[-7:], upload_id]
        self.save(connection, context)

    def link_plan(self, connection: Connection, plan: Plan, document: bool = False) -> None:
        context = self.load(connection, Identity(plan.owner_id, plan.session_id))
        identifiers = context.document_plans if document else context.plans
        if plan.id in identifiers:
            return
        identifiers[:] = [*identifiers[-7:], plan.id]
        if not document:
            context.pending_planning = None
        self.save(connection, context)

    def append_exchange(
        self, snapshot: Conversation, user: ConversationTurn, assistant: ConversationTurn
    ) -> Conversation:
        return self.publish(
            ConversationUpdate(snapshot, user, assistant, snapshot.pending_planning)
        )

    def publish(self, change: ConversationUpdate) -> Conversation:
        """追问、消息和新计划同事务提交，避免并发请求产生孤立或重复计划。"""
        with self.database.transaction() as connection:
            snapshot = change.snapshot
            identity = Identity(snapshot.owner_id, snapshot.session_id)
            context = self.load(connection, identity)
            if context.revision != snapshot.revision:
                raise ConflictError("session context changed during generation; retry the request")
            if change.plan is not None:
                if (change.plan.owner_id, change.plan.session_id) != (
                    snapshot.owner_id,
                    snapshot.session_id,
                ):
                    raise ConflictError("plan identity differs from conversation")
                insert_plan(self.database, connection, change.plan)
                context.plans = [*context.plans[-7:], change.plan.id]
            context.turns = [*context.turns[-30:], change.user, change.assistant]
            context.pending_planning = change.pending
            self.save(connection, context)
            return context

    def require_materials(self, plan_id: str, pending: PendingPlanning) -> None:
        """Worker 只为当前会话的最新失败计划设置追问，不覆盖用户已开始的新任务。"""
        with self.database.transaction() as connection:
            plan = self.database.load(connection, plan_id)
            context = self.load(connection, Identity(plan.owner_id, plan.session_id))
            if (
                plan.status != Status.FAILED
                or not context.plans
                or context.plans[-1] != plan.id
                or context.pending_planning is not None
            ):
                return
            context.pending_planning = pending
            self.save(connection, context)
