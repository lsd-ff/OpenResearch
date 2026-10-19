import json

from openresearch.agents.claim_freeze import ClaimFreezer
from openresearch.agents.context import AgentContext
from openresearch.agents.documents import DocumentAgent
from openresearch.agents.paper_analysis import PaperAnalyst
from openresearch.agents.repository import RepositoryAgent
from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.discovery import SearchRequest
from openresearch.models.runtime import TaskResult
from openresearch.sandbox.base import Sandbox
from openresearch.sources.discovery import SourceDiscovery
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


class LibrarianAgent:
    def __init__(
        self, settings: Settings, repository: Repository, files: ArtifactStore, sandbox: Sandbox
    ) -> None:
        self.documents = DocumentAgent(settings, repository, sandbox)
        self.analyst = PaperAnalyst(settings, files)
        self.repositories = RepositoryAgent(settings, files)
        self.discovery = SourceDiscovery(proxy=settings.source_proxy_url.get_secret_value() or None)
        self.claims = ClaimFreezer(settings, files)

    async def execute(self, context: AgentContext) -> TaskResult:
        plan, task, lease = context.plan, context.task, context.lease
        if task.type == "document_extract":
            return await self.documents.execute(plan, task, lease)
        if task.type == "paper_analyze":
            return await self.analyst.execute(plan, task)
        if task.type == "repository_fetch":
            return await self.repositories.execute(plan, task)
        if task.type == "claim_freeze":
            return await self.claims.execute(plan, task)
        if task.type != "source_discover" or "sources.search" not in task.contract.allowed_tools:
            raise ContractError("unsupported librarian task")
        request = SearchRequest.model_validate(task.inputs)
        results = await self.discovery.search(request)
        payload = json.dumps([item.model_dump() for item in results], ensure_ascii=False)
        return TaskResult(
            result=f"Retrieved {len(results)} sources from configured discovery providers.",
            structured_data=payload,
            outputs={"discovered_sources": payload},
        )
