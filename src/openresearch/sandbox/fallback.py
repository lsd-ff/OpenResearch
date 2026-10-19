import asyncio
import logging
import re

from openresearch.errors import SandboxError
from openresearch.models.resources import ManagedSandbox
from openresearch.models.runtime import ExecutionResult
from openresearch.sandbox.base import LogCallback, Sandbox

logger = logging.getLogger(__name__)


class FallbackSandbox:
    backend = "auto"

    def __init__(self, primary: Sandbox, fallback: Sandbox | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    async def create(self, plan_id: str, execution_id: str) -> str:
        try:
            return "dk-" + await self.primary.create(plan_id, execution_id)
        except Exception:
            if self.fallback is None:
                raise
            # 仅创建失败时切换后端；执行已经开始后不得换容器重跑或隐藏原失败。
            logger.warning("native Docker creation failed; trying configured OpenSandbox")
            return "os-" + await self.fallback.create(plan_id, execution_id)

    def select(self, sandbox_id: str) -> tuple[Sandbox, str]:
        if not re.fullmatch(r"(?:dk|os)-[a-zA-Z0-9_-]{1,128}", sandbox_id):
            raise SandboxError("invalid routed sandbox identifier")
        if sandbox_id.startswith("dk-"):
            return self.primary, sandbox_id[3:]
        if self.fallback is None:
            raise SandboxError("OpenSandbox fallback is disabled")
        return self.fallback, sandbox_id[3:]

    async def execute(self, sandbox_id: str, code: str, on_log: LogCallback) -> ExecutionResult:
        adapter, identifier = self.select(sandbox_id)
        return await adapter.execute(identifier, code, on_log)

    async def put_files(self, sandbox_id: str, files: dict[str, bytes]) -> None:
        adapter, identifier = self.select(sandbox_id)
        await adapter.put_files(identifier, files)

    async def get_file(self, sandbox_id: str, path: str, max_bytes: int) -> bytes:
        adapter, identifier = self.select(sandbox_id)
        return await adapter.get_file(identifier, path, max_bytes)

    async def close(self, sandbox_id: str) -> None:
        adapter, identifier = self.select(sandbox_id)
        await adapter.close(identifier)

    async def image_identity(self, sandbox_id: str) -> str | None:
        adapter, identifier = self.select(sandbox_id)
        return await adapter.image_identity(identifier)

    async def inventory(self) -> list[ManagedSandbox]:
        results: list[ManagedSandbox] = []
        available = False
        for prefix, adapter in (("dk-", self.primary), ("os-", self.fallback)):
            if adapter is None:
                continue
            try:
                async with asyncio.timeout(3):
                    items = await adapter.inventory()
            except Exception:
                logger.warning("sandbox backend inventory unavailable", extra={"backend": prefix})
                continue
            available = True
            results.extend(
                ManagedSandbox(prefix + item.id, item.execution_id, item.created_at)
                for item in items
            )
        if not available:
            raise SandboxError("all sandbox inventories are unavailable")
        return results

    async def health(self) -> bool:
        # 可选后端不可达时，健康的原生 Docker 仍须及时报告可用。
        for adapter in (self.primary, self.fallback):
            if adapter is None:
                continue
            try:
                async with asyncio.timeout(3):
                    if await adapter.health():
                        return True
            except Exception:
                continue
        return False

    async def shutdown(self) -> None:
        await self.primary.shutdown()
        if self.fallback:
            await self.fallback.shutdown()
