import asyncio
import json

from openresearch.agents.context import AgentContext
from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.chat import ChatReply
from openresearch.models.context import Identity
from openresearch.models.runtime import TaskResult
from openresearch.services.chat_context import ChatContext, ChatContextBuilder
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


class ChatAgent:
    """通用对话与 DAG 汇总共享同一模型和来源引用契约。"""

    def __init__(self, llm: LLMClient, repository: Repository, files: ArtifactStore) -> None:
        self.llm = llm
        self.repository = repository
        self.files = files
        self.contexts = ChatContextBuilder(repository, files)

    async def reply(self, context: ChatContext) -> ChatReply:
        reply = await self.llm.request_typed(context.messages, ChatReply)
        for citation in reply.citations:
            page = context.pages.get((citation.upload_id, citation.page))
            if page is None or citation.quote not in page or not citation.quote.strip():
                raise ContractError("chat citation does not match the supplied source page")
        return reply

    async def execute(self, context: AgentContext) -> TaskResult:
        task, plan = context.task, context.plan
        if task.type != "chat_answer" or "chat.answer" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to use the chat agent")
        identity = Identity(plan.owner_id, plan.session_id)
        snapshot = await asyncio.to_thread(self.repository.sessions.get, identity)
        prompt = await asyncio.to_thread(self.contexts.build, snapshot, task.description)
        artifacts = {
            key: (await asyncio.to_thread(self.files.read, plan.artifacts[key]))[:8000]
            for key in task.required_artifacts
        }
        prompt.messages.insert(
            -1,
            {"role": "user", "content": "TASK_ARTIFACT_DATA:\n" + json.dumps(artifacts)},
        )
        reply = await self.reply(prompt)
        return TaskResult(result=reply.response, outputs={"chat_answer": reply.model_dump_json()})
