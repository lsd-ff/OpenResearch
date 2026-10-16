import asyncio

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.chat import ChatReply
from openresearch.models.context import ConversationTurn, Identity
from openresearch.services.chat_context import ChatContextBuilder
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


class ChatService:
    def __init__(self, settings: Settings, repository: Repository, files: ArtifactStore) -> None:
        self.repository = repository
        self.contexts = ChatContextBuilder(repository, files)
        self.llm = LLMClient(settings)

    async def answer(self, identity: Identity, message: str) -> tuple[ChatReply, int]:
        snapshot = await asyncio.to_thread(self.repository.sessions.get, identity)
        context = await asyncio.to_thread(self.contexts.build, snapshot, message)
        reply = await self.llm.request_typed(context.messages, ChatReply)
        for citation in reply.citations:
            page = context.pages.get((citation.upload_id, citation.page))
            if page is None or citation.quote not in page or not citation.quote.strip():
                raise ContractError("chat citation does not match the supplied source page")
        # 生成期间会话可能发生变化；修订号校验避免旧回答覆盖新的上下文。
        saved = await asyncio.to_thread(
            self.repository.sessions.append_exchange,
            snapshot,
            ConversationTurn(role="user", content=message),
            ConversationTurn(role="assistant", content=reply.response),
        )
        return reply, saved.revision
