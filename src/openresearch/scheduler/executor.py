import asyncio
import logging
from contextlib import suppress

from openresearch.agents.claim_freeze import ClaimFreezer
from openresearch.agents.code import CodeAgent
from openresearch.agents.documents import DocumentAgent
from openresearch.agents.environment import EnvironmentAgent
from openresearch.agents.paper_analysis import PaperAnalyst
from openresearch.agents.repository import RepositoryAgent
from openresearch.agents.research_design import ResearchDesigner
from openresearch.agents.research_execute import ResearchExecutor
from openresearch.agents.research_prepare import ResearchPreparer
from openresearch.config import Settings
from openresearch.errors import ContractError, PlanningInputRequired, StaleExecutionError
from openresearch.llm.client import LLMClient
from openresearch.models.execution import ExecutionLease, TaskCompletion
from openresearch.models.routing import validate_role
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.research.source_context import document_context
from openresearch.sandbox.base import Sandbox
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository
from openresearch.tools.execution import ExecutionTool

logger = logging.getLogger(__name__)


class TaskExecutor:
    """完成一次已领取的任务；调度、续约和恢复由 Worker 负责。"""

    def __init__(
        self, settings: Settings, repository: Repository, files: ArtifactStore, sandbox: Sandbox
    ) -> None:
        self.repository = repository
        self.files = files
        self.code_agent = CodeAgent(LLMClient(settings))
        self.execution_tool = ExecutionTool(repository, sandbox)
        self.document_agent = DocumentAgent(settings, repository, sandbox)
        self.paper_analyst = PaperAnalyst(settings, files)
        self.repository_agent = RepositoryAgent(settings, files)
        self.environment_agent = EnvironmentAgent(settings)
        self.research_preparer = ResearchPreparer(settings, files)
        self.research_designer = ResearchDesigner(settings, files)
        self.research_executor = ResearchExecutor(settings, repository, files, sandbox)
        self.claim_freezer = ClaimFreezer(settings, files)

    async def execute(self, plan: Plan, task: Task, lease: ExecutionLease) -> None:
        try:
            async with asyncio.timeout(task.timeout_seconds):
                result = await self._route(plan, task, lease)
                await self._commit(task, lease, result)
        except StaleExecutionError:
            logger.info("discarded stale result", extra={"execution_id": lease.execution_id})
        except asyncio.CancelledError:
            # 保留租约直到过期，重启时才能区分旧进程与仍存活的 Worker。
            raise
        except Exception as exc:
            await self._fail(lease, exc)

    async def _route(self, plan: Plan, task: Task, lease: ExecutionLease) -> TaskResult:
        validate_role(task.type, task.assigned_to)
        if task.type in {
            "repository_fetch",
            "environment_lock",
            "claim_freeze",
            "research_prepare",
            "research_design",
            "research_execute",
        }:
            return await self._research(plan, task, lease)
        if task.type == "document_extract":
            return await self.document_agent.execute(plan, task, lease)
        if task.type == "paper_analyze":
            return await self.paper_analyst.execute(plan, task)
        if task.type == "code_generate":
            context = await asyncio.to_thread(document_context, plan, self.files)
            return await self.code_agent.execute(task, context)
        if task.type == "code_execute":
            code = await asyncio.to_thread(self.files.read, plan.artifacts["generated_code"])
            return await self.execution_tool.execute(task, lease, code)
        raise ContractError(f"unsupported task route: {task.type}/{task.assigned_to}")

    async def _research(self, plan: Plan, task: Task, lease: ExecutionLease) -> TaskResult:
        if task.type == "claim_freeze":
            return await self.claim_freezer.execute(plan, task)
        if task.type == "repository_fetch":
            return await self.repository_agent.execute(plan, task)
        if task.type == "environment_lock":
            return await self.environment_agent.execute(task, plan, self.files)
        if task.type == "research_design":
            return await self.research_designer.execute(plan, task)
        if task.type == "research_prepare":
            return await self.research_preparer.execute(plan, task)
        if task.type == "research_execute":
            return await self.research_executor.execute(plan, task, lease)
        raise ContractError(f"unsupported research task: {task.type}")

    async def _commit(self, task: Task, lease: ExecutionLease, result: TaskResult) -> None:
        if set(result.outputs) != set(task.output_artifacts):
            raise ContractError("agent output does not match its task contract")
        output = [
            await asyncio.to_thread(self.files.write, lease, key, value)
            for key, value in result.outputs.items()
        ]
        await asyncio.to_thread(self.repository.finish, lease, TaskCompletion(result, output))

    async def _fail(self, lease: ExecutionLease, exception: Exception) -> None:
        error = "task timed out" if isinstance(exception, TimeoutError) else str(exception)
        logger.warning("task failed", extra={"execution_id": lease.execution_id, "error": error})
        with suppress(StaleExecutionError):
            await asyncio.to_thread(self.repository.finish, lease, TaskCompletion(error=error))
            if isinstance(exception, PlanningInputRequired):
                await asyncio.to_thread(
                    self.repository.sessions.require_materials, lease.plan_id, exception.pending
                )
