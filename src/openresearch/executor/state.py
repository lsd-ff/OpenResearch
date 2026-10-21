import asyncio
from collections.abc import Callable

from openresearch.checkpoint.lease import SnapshotLease
from openresearch.errors import SandboxError


class PublishedState:
    def __init__(self, snapshot: SnapshotLease) -> None:
        self._snapshot = snapshot
        self.version = 0
        self._lock = asyncio.Lock()
        self._closed = False

    async def capture(self) -> tuple[int, SnapshotLease]:
        async with self._lock:
            if self._closed:
                raise SandboxError("published state is closed")
            return self.version, await self._snapshot.retain()

    async def publish(
        self, base_version: int, snapshot: SnapshotLease, committed: Callable[[int], None]
    ) -> bool:
        async with self._lock:
            if self._closed:
                raise SandboxError("published state is closed")
            if base_version != self.version:
                return False
            previous = self._snapshot
            self._snapshot = snapshot
            self.version += 1
            # 发布与事实回执之间不 await；即使随后清理被取消，也不能丢掉已发布事实。
            committed(self.version)
        await previous.close()
        return True

    async def close(self) -> None:
        async with self._lock:
            await self._snapshot.close()
            self._closed = True
