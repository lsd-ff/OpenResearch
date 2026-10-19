from openresearch.agents.chat import ChatAgent
from openresearch.agents.coder import CoderAgent
from openresearch.agents.context import AgentContext, TaskAgent
from openresearch.agents.data import DataAgent
from openresearch.agents.librarian import LibrarianAgent
from openresearch.config import Settings
from openresearch.llm.client import LLMClient
from openresearch.models.routing import canonical_role, validate_role
from openresearch.models.runtime import TaskResult
from openresearch.sandbox.base import Sandbox
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


class RoutedAgents:
    def __init__(
        self, settings: Settings, repository: Repository, files: ArtifactStore, sandbox: Sandbox
    ) -> None:
        librarian = LibrarianAgent(settings, repository, files, sandbox)
        self.coder = CoderAgent(settings, repository, files, sandbox)
        self.agents: dict[str, TaskAgent] = {
            "chat_agent": ChatAgent(LLMClient(settings), repository, files),
            "librarian_agent": librarian,
            "data_agent": DataAgent(settings, repository, files, librarian),
            "coder_agent": self.coder,
        }

    async def execute(self, context: AgentContext) -> TaskResult:
        task = context.task
        validate_role(task.type, task.assigned_to)
        # 旧计划的 research/sandbox 角色只在边界映射，新图使用四个明确的专业角色。
        role = canonical_role(task.type, task.assigned_to)
        return await self.agents[role].execute(context)
