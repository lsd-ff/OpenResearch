import asyncio
import math
import threading
from collections.abc import Callable
from types import TracebackType

from openresearch.errors import SandboxError
from openresearch.sandbox.core.engine import CoreEngine
from openresearch.sandbox.core.models import CommandResult, CoreConfig
from openresearch.sandbox.namespace import runtime_namespace


async def _settle[T](task: asyncio.Task[T], cancel: Callable[[], None] | None = None) -> T:
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if cancel:
            cancel()
        # to_thread 已进入 Docker/TTY，取消等待者不能让后台线程越过锁继续修改状态。
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


class CoreSandbox:
    """通过 async with 管理的独立持久终端；不接受宿主挂载和任意 Docker 参数。"""

    def __init__(self, config: CoreConfig) -> None:
        self.config = config.model_copy(update={"data_dir": config.data_dir.resolve()})
        self.namespace = runtime_namespace(self.config.data_dir)
        self._lock = asyncio.Lock()
        self._engine: CoreEngine | None = None
        self._closed = False

    @property
    def container_id(self) -> str:
        return self._engine.container_id if self._engine else ""

    async def __aenter__(self) -> "CoreSandbox":
        async with self._lock:
            if self._engine is not None or self._closed:
                raise SandboxError("core sandbox cannot be started twice")

            def start() -> None:
                self._engine = CoreEngine(self.config, self.namespace)
                self._engine.start()

            try:
                await _settle(asyncio.create_task(asyncio.to_thread(start)))
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
            return await _settle(task)

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
            return await _settle(task, cancel.set)

    async def _close_locked(self) -> None:
        if self._engine is not None:
            await _settle(asyncio.create_task(asyncio.to_thread(self._engine.close)))
            self._engine = None
        self._closed = True

    async def close(self) -> None:
        async with self._lock:
            await self._close_locked()
