import asyncio
import time

from openresearch.checkpoint.lease import SnapshotLease
from openresearch.checkpoint.manager import CheckpointManager
from openresearch.errors import SandboxError
from openresearch.executor.agent import PlanningAgent
from openresearch.executor.gates import Gatekeeper
from openresearch.executor.journal import RunJournal
from openresearch.executor.models import DAG, ExecutorConfig
from openresearch.executor.race import StrategyRace
from openresearch.executor.receipts import ExecutionError, GraphReceipt
from openresearch.executor.resources import effective_graph
from openresearch.executor.scheduler import DAGScheduler
from openresearch.executor.slots import SandboxSlot
from openresearch.executor.state import PublishedState
from openresearch.sandbox.core.session import CoreSandbox
from openresearch.sandbox.core.transactions import settle
from openresearch.watchdog.checker import Checker


class Engine:
    def __init__(
        self,
        config: ExecutorConfig,
        *,
        gates: Gatekeeper | None = None,
        agent: PlanningAgent | None = None,
        checker: Checker | None = None,
    ) -> None:
        self.config, self.agent, self.checker = config, agent, checker
        self.gates = gates or Gatekeeper(config.resources)
        self.last_receipt: GraphReceipt | None = None
        self._state: PublishedState | None = None
        self._race: StrategyRace | None = None
        self._seed: SandboxSlot | None = None
        self._task: asyncio.Task[GraphReceipt] | None = None
        self._journal: RunJournal | None = None
        self._open = self._entered = self._closed = False
        self._lifecycle = asyncio.Lock()

    async def __aenter__(self) -> "Engine":
        if self._entered or self._closed:
            raise SandboxError("executor cannot be entered twice")
        self._open = self._entered = True
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def execute_graph(self, graph: DAG) -> GraphReceipt:
        if not self._open or self._task is not None:
            raise SandboxError("executor needs a fresh active context for one graph")
        resolved = effective_graph(graph, self.config, self.gates)
        journal = RunJournal(self.config, graph, resolved)
        self._journal = journal
        self.last_receipt = journal.receipt
        self._task = asyncio.create_task(self._run(resolved, journal))
        task = self._task

        def cancel() -> None:
            task.cancel()

        try:
            return await settle(task, cancel)
        except asyncio.CancelledError:
            self._finalize_unstarted_cancellation()
            raise

    def _finalize_unstarted_cancellation(self) -> None:
        journal = self._journal
        if journal is not None and journal.receipt.status == "running":
            receipt = journal.receipt
            receipt.status = "canceled"
            receipt.error_type = "CancelledError"
            receipt.finished_at = time.time()
            for node in receipt.nodes:
                if node.status == "pending":
                    node.status = "blocked"
            journal.flush()

    async def _run(self, graph: DAG, journal: RunJournal) -> GraphReceipt:
        receipt = journal.receipt
        try:
            async with asyncio.timeout(self.config.graph_timeout_seconds):
                seed_config = self.config.sandbox.model_copy(
                    update={"data_dir": journal.root / "seed"}
                )
                self._seed = SandboxSlot(
                    CoreSandbox(seed_config), self.gates, 1, int(seed_config.gpu_enabled)
                )
                try:
                    await self._seed.start()
                    baseline = await CheckpointManager(self._seed.sandbox).commit_and_pin(
                        "__base__"
                    )
                    self._state = PublishedState(baseline)
                finally:
                    await settle(asyncio.create_task(self._seed.close()))
                self._seed = None
                self._race = StrategyRace(journal, self.gates, self.checker)
                await DAGScheduler(journal, self._state, self._race, self.agent).execute(graph)
            receipt.status = "completed"
            return receipt
        except asyncio.CancelledError:
            receipt.status = "canceled"
            receipt.error_type = "CancelledError"
            raise
        except Exception as exc:
            receipt.status = "timed_out" if isinstance(exc, TimeoutError) else "failed"
            receipt.error_type = type(exc).__name__
            raise ExecutionError(receipt) from exc
        finally:
            for node in receipt.nodes:
                if node.status == "pending":
                    node.status = "blocked"
            receipt.finished_at = time.time()
            journal.flush()

    async def snapshot(self) -> SnapshotLease:
        if not self._open or self._state is None:
            raise SandboxError("executor has no available published state")
        _, snapshot = await self._state.capture()
        return snapshot

    async def _cleanup(self) -> None:
        if self._task is not None:
            if not self._task.done():
                self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._finalize_unstarted_cancellation()
        errors: list[Exception] = []
        for resource in (self._race, self._seed, self._state):
            if resource is not None:
                try:
                    await resource.close()
                except Exception as exc:
                    errors.append(exc)
        if errors:
            raise SandboxError(
                "executor cleanup failed: " + ", ".join(type(exc).__name__ for exc in errors)
            )
        self._seed = None

    async def close(self) -> None:
        async with self._lifecycle:
            self._open = False
            if not self._closed:
                await settle(asyncio.create_task(self._cleanup()))
                self._closed = True
