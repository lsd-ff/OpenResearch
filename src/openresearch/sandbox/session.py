import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import ExecutionResult
from openresearch.sandbox.base import Sandbox
from openresearch.storage.repository import Repository

logger = logging.getLogger(__name__)


@dataclass
class RunningSandbox:
    id: str
    lease: ExecutionLease
    repository: Repository
    adapter: Sandbox
    runtime_profile: str | None = None

    async def _log(self, stream: str, message: str) -> None:
        await asyncio.to_thread(self.repository.append_log, self.lease, message, stream)

    async def execute(self, code: str) -> ExecutionResult:
        return await self.adapter.execute(self.id, code, self._log)

    async def put_files(self, files: dict[str, bytes]) -> None:
        await self.adapter.put_files(self.id, files)

    async def get_file(self, path: str, max_bytes: int) -> bytes:
        return await self.adapter.get_file(self.id, path, max_bytes)


class SandboxSessions:
    """一个租约对应一个受管容器，所有工具复用同一套取消与清理规则。"""

    def __init__(self, repository: Repository, adapter: Sandbox) -> None:
        self.repository = repository
        self.adapter = adapter

    @asynccontextmanager
    async def open(
        self, lease: ExecutionLease, runtime_profile: str | None = None
    ) -> AsyncIterator[RunningSandbox]:
        creation = asyncio.create_task(
            self.adapter.create(lease.plan_id, lease.execution_id, runtime_profile)
            if runtime_profile
            else self.adapter.create(lease.plan_id, lease.execution_id)
        )
        sandbox_id: str | None = None
        registered = False
        try:
            try:
                # 阻塞线程可能已经创建容器，取消后仍需拿到 ID 才能可靠清理。
                sandbox_id = await asyncio.shield(creation)
            except asyncio.CancelledError:
                sandbox_id = await creation
                raise
            await asyncio.to_thread(
                self.repository.register_sandbox, lease, sandbox_id, self.adapter.backend
            )
            registered = True
            yield RunningSandbox(sandbox_id, lease, self.repository, self.adapter, runtime_profile)
        finally:
            if sandbox_id is not None:
                await self._protected_cleanup(sandbox_id, registered)

    async def _protected_cleanup(self, sandbox_id: str, registered: bool) -> None:
        cleanup = asyncio.create_task(self._cleanup(sandbox_id, registered))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    async def _cleanup(self, sandbox_id: str, registered: bool) -> None:
        error = ""
        try:
            async with asyncio.timeout(20):
                await self.adapter.close(sandbox_id)
        except Exception as exc:
            error = type(exc).__name__
            logger.exception("sandbox cleanup failed", extra={"sandbox_id": sandbox_id})
        if registered:
            await asyncio.to_thread(self.repository.mark_sandbox_closed, sandbox_id, error)
