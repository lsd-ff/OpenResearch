import asyncio
import json
import logging
from contextlib import suppress

from openresearch.agents.context import AgentContext
from openresearch.agents.router import RoutedAgents
from openresearch.agents.scopes import scoped_context, scoped_result
from openresearch.config import Settings
from openresearch.errors import PlanningInputRequired, StaleExecutionError
from openresearch.models.execution import ExecutionLease, TaskCompletion, validate_output_keys
from openresearch.models.output_files import OutputFiles
from openresearch.models.runtime import Artifact, Plan, Task, TaskResult
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
        context = AgentContext(plan, task, lease)
        result = await self.agents.execute(scoped_context(context))
        return scoped_result(context, result)

    async def _commit(self, task: Task, lease: ExecutionLease, result: TaskResult) -> None:
        validate_output_keys(task.output_artifacts, result.outputs.keys(), result.success)
        output = [
            await asyncio.to_thread(self.files.write, lease, key, value)
            for key, value in result.outputs.items()
        ]
        self._describe_files(lease, result, output)
        await asyncio.to_thread(self.repository.finish, lease, TaskCompletion(result, output))

    @staticmethod
    def _describe_files(
        lease: ExecutionLease, result: TaskResult, artifacts: list[Artifact]
    ) -> None:
        collection = next(
            (
                item
                for item in artifacts
                if item.key == "execution_files" or item.key.endswith("__execution_files")
            ),
            None,
        )
        if collection is None:
            return
        bundle = OutputFiles.model_validate_json(result.outputs[collection.key])
        metadata = json.loads(result.structured_data or "{}")
        metadata["output_files"] = [
            {
                "name": item.path,
                "size": item.size,
                "sha256": item.sha256,
                "url": f"/api/plans/{lease.plan_id}/artifacts/{collection.id}/files/{index}",
            }
            for index, item in enumerate(bundle.files)
        ]
        result.structured_data = json.dumps(metadata, ensure_ascii=False)

    async def _fail(self, lease: ExecutionLease, exception: Exception) -> None:
        error = "task timed out" if isinstance(exception, TimeoutError) else str(exception)
        logger.warning("task failed", extra={"execution_id": lease.execution_id, "error": error})
        with suppress(StaleExecutionError):
            await asyncio.to_thread(self.repository.finish, lease, TaskCompletion(error=error))
            if isinstance(exception, PlanningInputRequired):
                await asyncio.to_thread(
                    self.repository.sessions.require_materials, lease.plan_id, exception.pending
                )
