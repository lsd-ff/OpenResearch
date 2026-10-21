import asyncio
import re
from dataclasses import dataclass, field, replace

from openresearch.config import Settings
from openresearch.errors import ModelError
from openresearch.models.chat import SourceCitation
from openresearch.models.context import ConversationTurn
from openresearch.models.runtime import Plan
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.agent import PlannerAgent
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
        self.graph_planner = PlannerAgent(settings, repository)

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
                if outcome.plan is not None:
                    outcome = replace(outcome, plan=await self.graph_planner.refine(outcome.plan))
        except TimeoutError:
            raise ModelError(
                "automatic planning timed out; retry or provide the source link"
            ) from None
        if outcome.chat:
            reply, revision = await self.chat.answer(submission.identity, submission.intent)
            return MessageResult("chat", reply.response, revision, citations=reply.citations)
        # 是否执行由代码块外的用户指令决定，待分析源码不能自行触发入队。
        instructions = re.sub(r"```.*?```", "", submission.intent, flags=re.S)
        auto_run = (
            outcome.plan is not None
            and not outcome.plan.approval.required
            and (
                outcome.plan.intent_type == "Framework_Comparison"
                or any(node.type == "research_gap_repair" for node in outcome.plan.nodes)
                or (
                    any(node.type == "code_execute" for node in outcome.plan.nodes)
                    and bool(
                        re.search(r"直接执行|执行完|自动执行|跑通|执行.{0,40}修复", instructions)
                    )
                )
                or (
                    any(node.type == "research_design" for node in outcome.plan.nodes)
                    and bool(
                        re.search(
                            r"跑通|执行完|直接执行|跑.{0,20}测试|run.{0,20}tests?",
                            instructions,
                            re.I,
                        )
                    )
                )
            )
            and not re.search(
                r"只.{0,4}规划|先.{0,4}规划|不要.{0,4}执行|不执行|仅.{0,4}计划", instructions
            )
        )
        response = (
            "已开始执行，完成后会在当前聊天中返回实际执行报告。\n"
            + outcome.response.replace("可在右侧运行。", "执行进度会在右侧更新。")
            if auto_run
            else outcome.response
        )
        saved = await asyncio.to_thread(
            self.repository.sessions.publish,
            ConversationUpdate(
                outcome.snapshot,
                ConversationTurn(role="user", content=submission.intent),
                ConversationTurn(role="assistant", content=response),
                outcome.pending,
                outcome.plan,
            ),
        )
        plan = outcome.plan
        if auto_run and plan is not None:
            # 会话与计划已同事务发布，再使用标准队列入口；关闭页面不影响 Worker 执行。
            await asyncio.to_thread(self.repository.queue_plan, plan.id, plan.owner_id)
            plan = await asyncio.to_thread(self.repository.get_plan, plan.id, plan.owner_id)
        return MessageResult(
            "plan" if plan else "clarification",
            response,
            saved.revision,
            plan,
        )
