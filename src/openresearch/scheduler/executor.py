import asyncio
import logging
from contextlib import suppress

from openresearch.agents.context import AgentContext
from openresearch.agents.router import RoutedAgents
from openresearch.config import Settings
from openresearch.errors import ContractError, PlanningInputRequired, StaleExecutionError
from openresearch.models.execution import ExecutionLease, TaskCompletion
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.sandbox.base import Sandbox
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository

logger = logging.getLogger(__name__)


class TaskExecutor:
    """完成一次已领取的任务；调度、续约和恢复由 Worker 负责。"""

    def __init__(
        self, settings: Settings, repository: Repository, files: ArtifactStore, sandbox: Sandbox
    ) -> None:
        self.repository = repository
        self.files = files
        self.agents = RoutedAgents(settings, repository, files, sandbox)
        self.research_executor = self.agents.coder.research_executor

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
        return await self.agents.execute(AgentContext(plan, task, lease))

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
