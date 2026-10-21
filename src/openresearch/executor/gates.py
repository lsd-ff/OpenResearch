import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class ResourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_concurrent_io: int = Field(default=5, ge=1, le=4096, strict=True)
    max_gpu_tasks: int = Field(default=1, ge=0, le=4096, strict=True)


@dataclass(frozen=True)
class GateUsage:
    capacity: int
    in_use: int
    waiting: int


@dataclass
class _Waiter:
    weight: int
    future: asyncio.Future[None]
    granted: bool = False


class WeightedSemaphore:
    """绑定同一事件循环的 FIFO 加权配额；不探测或宣称物理设备可用。"""

    def __init__(self, capacity: int) -> None:
        if type(capacity) is not int or capacity < 0:
            raise ValueError("invalid resource capacity")
        self._capacity = self._available = capacity
        self._waiters: deque[_Waiter] = deque()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def usage(self) -> GateUsage:
        return GateUsage(self._capacity, self._capacity - self._available, len(self._waiters))

    def validate_weight(self, weight: int) -> None:
        if type(weight) is not int or not 0 <= weight <= self._capacity:
            raise ValueError("resource weight is invalid or exceeds capacity")

    def _bind_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("weighted gate cannot cross event loops")
        return loop

    async def acquire(self, weight: int = 1) -> None:
        self.validate_weight(weight)
        loop = self._bind_loop()
        if weight == 0:
            return
        if not self._waiters and weight <= self._available:
            self._available -= weight
            return
        waiter = _Waiter(weight, loop.create_future())
        self._waiters.append(waiter)
        self._wake()
        try:
            await waiter.future
        except BaseException:
            # Future 已获配额但等待者尚未恢复时也可能取消，此时必须归还已预留的权重。
            if waiter.granted:
                self._available += weight
            else:
                with suppress(ValueError):
                    self._waiters.remove(waiter)
            self._wake()
            raise

    def release(self, weight: int = 1) -> None:
        self.validate_weight(weight)
        self._bind_loop()
        if weight > self._capacity - self._available:
            raise ValueError("cannot release unreserved resource weight")
        self._available += weight
        self._wake()

    def _wake(self) -> None:
        while self._waiters:
            waiter = self._waiters[0]
            if waiter.future.cancelled():
                self._waiters.popleft()
                continue
            # 不让后来的小请求越过队首；避免大权重请求长期得不到完整配额。
            if waiter.weight > self._available:
                return
            self._waiters.popleft()
            self._available -= waiter.weight
            waiter.granted = True
            waiter.future.set_result(None)

    @asynccontextmanager
    async def hold(self, weight: int = 1) -> AsyncIterator[None]:
        await self.acquire(weight)
        try:
            yield
        finally:
            self.release(weight)


class Gatekeeper:
    def __init__(self, config: ResourceConfig | None = None) -> None:
        self.config = config or ResourceConfig()
        self._io = WeightedSemaphore(self.config.max_concurrent_io)
        self._gpu = WeightedSemaphore(self.config.max_gpu_tasks)

    @property
    def io_usage(self) -> GateUsage:
        return self._io.usage

    @property
    def gpu_usage(self) -> GateUsage:
        return self._gpu.usage

    async def acquire_io(self, weight: int = 1) -> None:
        await self._io.acquire(weight)

    def release_io(self, weight: int = 1) -> None:
        self._io.release(weight)

    async def acquire_gpu(self, weight: int = 1) -> None:
        await self._gpu.acquire(weight)

    def release_gpu(self, weight: int = 1) -> None:
        self._gpu.release(weight)

    @asynccontextmanager
    async def reserve(self, io_weight: int = 1, gpu_weight: int = 0) -> AsyncIterator[None]:
        self._io.validate_weight(io_weight)
        self._gpu.validate_weight(gpu_weight)
        # 始终按 IO -> GPU 获取，并在后者失败/取消时归还前者。
        async with self._io.hold(io_weight), self._gpu.hold(gpu_weight):
            yield
