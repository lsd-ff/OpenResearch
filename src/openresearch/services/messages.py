import asyncio
from dataclasses import dataclass, field

from openresearch.config import Settings
from openresearch.errors import ModelError
from openresearch.models.chat import SourceCitation
from openresearch.models.context import ConversationTurn
from openresearch.models.runtime import Plan
from openresearch.models.submissions import PlanSubmission
from openresearch.services.auto_planning import AutoPlanner
from openresearch.services.chat import ChatService
from openresearch.storage.conversations import ConversationUpdate
from openresearch.storage.repository import Repository


@dataclass(frozen=True)
class MessageResult:
    kind: str
    response: str
    revision: int
    plan: Plan | None = None
    citations: list[SourceCitation] = field(default_factory=list)


class MessageService:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        chat: ChatService,
        planner: AutoPlanner,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.chat = chat
        self.planner = planner

    async def send(self, submission: PlanSubmission) -> MessageResult:
        """服务端统一路由；浏览器无需猜测追问回复属于哪个工作流。"""
        snapshot = await asyncio.to_thread(self.repository.sessions.get, submission.identity)
        if (
            submission.intent.strip() in {"取消", "取消当前任务", "不用了", "cancel"}
            and snapshot.pending_planning
        ):
            response = "已取消当前规划追问，可以直接提出新的目标。"
            saved = await asyncio.to_thread(
                self.repository.sessions.publish,
                ConversationUpdate(
                    snapshot,
                    ConversationTurn(role="user", content=submission.intent),
                    ConversationTurn(role="assistant", content=response),
                    None,
                ),
            )
            return MessageResult("chat", response, saved.revision)
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds * 3 + 120):
                outcome = await self.planner.resolve(submission, snapshot)
        except TimeoutError:
            raise ModelError(
                "automatic planning timed out; retry or provide the source link"
            ) from None
        if outcome.chat:
            reply, revision = await self.chat.answer(submission.identity, submission.intent)
            return MessageResult("chat", reply.response, revision, citations=reply.citations)
        saved = await asyncio.to_thread(
            self.repository.sessions.publish,
            ConversationUpdate(
                outcome.snapshot,
                ConversationTurn(role="user", content=submission.intent),
                ConversationTurn(role="assistant", content=outcome.response),
                outcome.pending,
                outcome.plan,
            ),
        )
        return MessageResult(
            "plan" if outcome.plan else "clarification",
            outcome.response,
            saved.revision,
            outcome.plan,
        )
