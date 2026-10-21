import asyncio
import math
import threading
import time
from collections.abc import Callable
from types import TracebackType

from openresearch.checkpoint.lease import SnapshotLease
from openresearch.errors import ResourceBusyError, SandboxError
from openresearch.sandbox.core.engine import CoreEngine
from openresearch.sandbox.core.models import CommandResult, CoreConfig
from openresearch.sandbox.core.transactions import settle
from openresearch.sandbox.namespace import runtime_namespace
from openresearch.storage.localpath import filesystem_path


class CoreSandbox:
    """通过 async with 管理的独立持久终端；不接受宿主挂载和任意 Docker 参数。"""

    def __init__(self, config: CoreConfig, *, snapshot: SnapshotLease | None = None) -> None:
        self.config = config.model_copy(update={"data_dir": filesystem_path(config.data_dir)})
        self.namespace = runtime_namespace(self.config.data_dir)
        self._lock = asyncio.Lock()
        self._engine: CoreEngine | None = None
        self._closed = False
        self._snapshot = snapshot

    @property
    def container_id(self) -> str:
        return self._engine.container_id if self._engine else ""

    @property
    def gpu_reservation_id(self) -> str | None:
        return self._engine._gpu.id or None if self._engine else None

    async def __aenter__(self) -> "CoreSandbox":
        async with self._lock:
            if self._engine is not None or self._closed:
                raise SandboxError("core sandbox cannot be started twice")

            def start() -> None:
                self._engine = CoreEngine(self.config, self.namespace, self._snapshot)
                self._engine.start()

            try:
                deadline = time.monotonic() + self.config.gpu_wait_seconds
                while True:
                    try:
                        await settle(asyncio.create_task(asyncio.to_thread(start)))
                        break
                    except ResourceBusyError:
                        # 只重试真实占用；本次创建必须完整清理后才能进入可取消的异步等待。
                        await self._release_engine()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ResourceBusyError("core GPU admission timed out") from None
                        await asyncio.sleep(min(0.25, remaining))
            except BaseException:
                await self._close_locked()
                raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def run_exclusive[T](self, operation: Callable[[CoreEngine], T]) -> T:
        """底层检查点事务使用；命令、提交、重建和关闭共享同一把锁。"""
        async with self._lock:
            if self._engine is None or self._closed:
                raise SandboxError("core sandbox is not running")
            task = asyncio.create_task(asyncio.to_thread(operation, self._engine))
            return await settle(task)

    async def execute(self, command: str, timeout_seconds: float | None = None) -> CommandResult:
        if "\x00" in command or len(command.encode()) > self.config.max_command_bytes:
            raise ValueError("core command exceeds its contract")
        timeout = self.config.timeout_seconds if timeout_seconds is None else timeout_seconds
        if not math.isfinite(timeout) or not 0 < timeout <= self.config.timeout_seconds:
            raise ValueError("invalid core command timeout")
        cancel = threading.Event()
        async with self._lock:
            if self._engine is None or self._closed:
                raise SandboxError("core sandbox is not running")
            task = asyncio.create_task(
                asyncio.to_thread(self._engine.execute, command, timeout, cancel)
            )
            return await settle(task, cancel.set)

    async def _release_engine(self) -> None:
        if self._engine is not None:
            await settle(asyncio.create_task(asyncio.to_thread(self._engine.close)))
            self._engine = None

    async def _close_locked(self) -> None:
        await self._release_engine()
        self._closed = True

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()
