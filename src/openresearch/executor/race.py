import asyncio
import time
from dataclasses import dataclass

from openresearch.checkpoint.lease import SnapshotLease
from openresearch.checkpoint.manager import CheckpointManager
from openresearch.errors import SandboxError
from openresearch.executor.gates import Gatekeeper
from openresearch.executor.journal import RunJournal
from openresearch.executor.models import Node
from openresearch.executor.receipts import (
    AttemptReceipt,
    CommandEvidence,
    NodeExecutionError,
    NodeReceipt,
)
from openresearch.executor.slots import CandidateSlot
from openresearch.sandbox.core.transactions import settle
from openresearch.watchdog.checker import Checker
from openresearch.watchdog.models import WatchdogError
from openresearch.watchdog.service import Watchdog


@dataclass
class Candidate:
    snapshot: SnapshotLease
    receipt: AttemptReceipt
    transferred: bool = False


class StrategyRace:
    def __init__(self, journal: RunJournal, gates: Gatekeeper, checker: Checker | None) -> None:
        self.journal, self.gates, self.checker = journal, gates, checker
        self._active: dict[str, CandidateSlot] = {}

    async def run(
        self,
        node: Node,
        record: NodeReceipt,
        commands: tuple[str, ...],
        version: int,
        base: SnapshotLease,
    ) -> Candidate:
        attempts = self.journal.reserve(record, commands, version)
        tasks = [
            asyncio.create_task(self._candidate(node, command, attempt, base))
            for command, attempt in zip(commands, attempts, strict=True)
        ]
        completed: asyncio.Queue[asyncio.Task[Candidate | None]] = asyncio.Queue()
        for task in tasks:
            task.add_done_callback(completed.put_nowait)
        try:
            pending = set(tasks)
            winner: Candidate | None = None
            while pending and winner is None:
                task = await completed.get()
                pending.remove(task)
                result = task.result()
                if result is not None:
                    winner = result
            if winner is None:
                raise NodeExecutionError("all candidate strategies failed")
            await self._cleanup(tasks, attempts, winner)
            return winner
        except BaseException:
            # 已完成任务也可能携带租约；取消 done Task 不会释放其中的结果对象。
            await settle(asyncio.create_task(self._cleanup(tasks, attempts, None)))
            raise

    async def _cleanup(
        self,
        tasks: list[asyncio.Task[Candidate | None]],
        attempts: list[AttemptReceipt],
        keep: Candidate | None,
    ) -> None:
        for task in tasks:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors: list[Exception] = []
        for attempt in attempts:
            try:
                await self._close_slot(attempt.id)
            except Exception as exc:
                errors.append(exc)
        for result in results:
            if isinstance(result, Candidate) and result is not keep:
                if result.receipt.status == "succeeded":
                    result.receipt.status = "discarded"
                try:
                    await result.snapshot.close()
                except Exception as exc:
                    errors.append(exc)
        self.journal.flush()
        if errors:
            raise SandboxError(
                "candidate cleanup failed: " + ", ".join(type(exc).__name__ for exc in errors)
            )

    async def _close_slot(self, attempt_id: str) -> None:
        slot = self._active.get(attempt_id)
        if slot is not None:
            await slot.close()
            del self._active[attempt_id]

    async def close(self) -> None:
        for attempt_id in list(self._active):
            await self._close_slot(attempt_id)

    async def _candidate(
        self, node: Node, command: str, receipt: AttemptReceipt, base: SnapshotLease
    ) -> Candidate | None:
        snapshot: SnapshotLease | None = None
        config = self.journal.config.sandbox.model_copy(
            update={"data_dir": receipt.audit_directory}
        )
        slot = CandidateSlot(base, config, self.gates, node)
        self._active[receipt.id] = slot
        try:
            try:
                await slot.start()
                sandbox = slot.sandbox
                receipt.container_id = sandbox.container_id
                receipt.status = "running"
                receipt.started_at = time.time()
                self.journal.flush()
                manager = CheckpointManager(sandbox)
                result = await Watchdog(sandbox, manager, checker=self.checker).execute(
                    command, node.id
                )
                receipt.result = CommandEvidence.from_result(result)
                snapshot = await manager.commit_and_pin(node.id)
                receipt.snapshot_image = snapshot.snapshot.image_id
            finally:
                await settle(asyncio.create_task(self._close_slot(receipt.id)))
            receipt.status = "succeeded"
            return Candidate(snapshot, receipt)
        except asyncio.CancelledError:
            receipt.status = "canceled"
            if snapshot is not None:
                await snapshot.close()
            raise
        except Exception as exc:
            receipt.status = "failed"
            receipt.error_type = type(exc).__name__
            if isinstance(exc, WatchdogError) and exc.result is not None:
                receipt.result = CommandEvidence.from_result(exc.result)
            if snapshot is not None:
                await snapshot.close()
            if receipt.id in self._active:
                raise
            return None
        finally:
            receipt.finished_at = time.time()
            self.journal.flush()
