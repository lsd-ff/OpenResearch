import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import dataclass

from openresearch.config import Settings
from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import new_id
from openresearch.sandbox.base import Sandbox
from openresearch.scheduler.executor import TaskExecutor
from openresearch.storage.files import ArtifactStore
from openresearch.storage.garbage import ArtifactGarbageCollector
from openresearch.storage.repository import Repository

logger = logging.getLogger(__name__)


@dataclass
class ActiveTask:
    lease: ExecutionLease
    future: asyncio.Task[None]


class Worker:
    def __init__(
        self, settings: Settings, repository: Repository, files: ArtifactStore, sandbox: Sandbox
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.sandbox = sandbox
        self.id = f"worker-{new_id()}"
        self.executor = TaskExecutor(settings, repository, files, sandbox)
        self.active: dict[str, ActiveTask] = {}
        self.stopping = asyncio.Event()
        self.garbage = ArtifactGarbageCollector(repository.database, files.root)

    async def _cleanup(self) -> None:
        identifiers = await asyncio.to_thread(
            self.repository.cleanup_candidates, self.sandbox.backend
        )
        for identifier in identifiers:
            try:
                async with asyncio.timeout(15):
                    await self.sandbox.close(identifier)
                await asyncio.to_thread(self.repository.mark_sandbox_closed, identifier)
            except Exception:
                logger.exception(
                    "deferred sandbox cleanup failed", extra={"sandbox_id": identifier}
                )
        await self._reconcile_sandboxes()

    async def _reconcile_sandboxes(self) -> None:
        try:
            for resource in await self.sandbox.inventory():
                if time.time() - resource.created_at < self.settings.lease_seconds * 2:
                    continue
                if await asyncio.to_thread(self.repository.execution_live, resource.execution_id):
                    continue
                await self.sandbox.close(resource.id)
                await asyncio.to_thread(self.repository.mark_sandbox_closed, resource.id)
        except Exception:
            logger.exception("sandbox label reconciliation failed")

    async def _renew(self) -> None:
        for execution_id, active in list(self.active.items()):
            if active.future.done():
                with suppress(asyncio.CancelledError):
                    active.future.result()
                del self.active[execution_id]
                continue
            if active.future.cancelling():
                continue
            current = await asyncio.to_thread(
                self.repository.renew, active.lease, self.settings.lease_seconds
            )
            if not current and not await asyncio.to_thread(
                self.repository.execution_committed, active.lease
            ):
                # 提交事务可能已终止租约，但 to_thread 尚未返回；不可误取消已经提交的结果。
                # 重复 cancel 会打断 finally 内的容器清理；每次执行只取消一次。
                active.future.cancel()

    async def tick(self) -> None:
        """一次调度循环：续约、恢复、按全局配额领取任务。"""
        await self._renew()
        await asyncio.to_thread(self.repository.recover_expired)
        while len(self.active) < self.settings.max_concurrent_tasks:
            claimed = await asyncio.to_thread(
                self.repository.claim_next,
                self.id,
                self.settings.lease_seconds,
                self.settings.max_concurrent_tasks,
            )
            if claimed is None:
                return
            plan, task = claimed
            lease = ExecutionLease.for_task(plan, task, self.id)
            future = asyncio.create_task(self.executor.execute(plan, task, lease))
            self.active[lease.execution_id] = ActiveTask(lease, future)

    async def _housekeeping(self) -> None:
        while not self.stopping.is_set():
            await self._cleanup()
            await asyncio.to_thread(self.garbage.collect)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), 10)

    async def run(self) -> None:
        """运行独立 Worker；清理任务与续约循环并行，避免慢清理拖垮租约。"""
        logger.info("worker started", extra={"worker_id": self.id})
        housekeeping = asyncio.create_task(self._housekeeping())
        try:
            while not self.stopping.is_set():
                if housekeeping.done():
                    housekeeping.result()
                await self.tick()
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self.stopping.wait(), self.settings.poll_interval_seconds
                    )
        finally:
            housekeeping.cancel()
            await asyncio.gather(housekeeping, return_exceptions=True)
            await self._shutdown()

    async def _shutdown(self) -> None:
        for active in self.active.values():
            if not active.future.done() and not active.future.cancelling():
                active.future.cancel()
        await asyncio.gather(
            *(active.future for active in self.active.values()), return_exceptions=True
        )
        await self.sandbox.shutdown()
        logger.info("worker stopped", extra={"worker_id": self.id})

    def stop(self) -> None:
        self.stopping.set()
