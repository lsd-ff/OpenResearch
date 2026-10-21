import asyncio
import time

from openresearch.executor.agent import PlanningAgent
from openresearch.executor.journal import RunJournal
from openresearch.executor.models import DAG, Node
from openresearch.executor.race import Candidate, StrategyRace
from openresearch.executor.receipts import NodeExecutionError, NodeReceipt
from openresearch.executor.state import PublishedState
from openresearch.sandbox.core.transactions import settle


def _record_failure(record: NodeReceipt, exc: BaseException) -> None:
    if record.status != "completed":
        if isinstance(exc, asyncio.CancelledError):
            record.status = "canceled"
        elif isinstance(exc, TimeoutError):
            record.status = "timed_out"
        else:
            record.status = "failed"
    record.error_type = type(exc).__name__


class DAGScheduler:
    def __init__(
        self,
        journal: RunJournal,
        state: PublishedState,
        race: StrategyRace,
        agent: PlanningAgent | None,
    ) -> None:
        self.journal, self.state, self.race, self.agent = journal, state, race, agent

    async def execute(self, graph: DAG) -> None:
        pending = {node.id: node for node in graph.nodes}
        completed: set[str] = set()
        records = {node.id: node for node in self.journal.receipt.nodes}
        active: dict[asyncio.Task[None], str] = {}
        try:
            while pending or active:
                for node in list(pending.values()):
                    if len(active) >= self.journal.config.max_parallel_nodes:
                        break
                    if set(node.dependencies) <= completed:
                        active[
                            asyncio.create_task(
                                self._node(node, records[node.id], len(graph.nodes))
                            )
                        ] = node.id
                        del pending[node.id]
                if not active:
                    raise NodeExecutionError("no runnable node remains")
                done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                    completed.add(active.pop(task))
        finally:

            async def drain() -> None:
                for task in active:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*active, return_exceptions=True)

            await settle(asyncio.create_task(drain()))

    async def _commands(self, node: Node) -> tuple[str, ...]:
        commands = node.commands
        if not commands:
            if self.agent is None:
                raise NodeExecutionError("an unresolved node requires a planning agent")
            commands = await self.agent.generate_strategies(node.id)
            if not 2 <= len(commands) <= 3:
                raise NodeExecutionError("the agent must provide two or three alternatives")
            commands = Node(id=node.id, commands=commands).commands
        if any(
            len(command.encode()) > self.journal.config.sandbox.max_command_bytes
            for command in commands
        ):
            raise NodeExecutionError("candidate exceeds configured command budget")
        self.journal.resolve(node.id, commands)
        return commands

    async def _node(self, node: Node, record: NodeReceipt, max_rounds: int) -> None:
        record.status = "running"
        record.started_at = time.time()
        self.journal.flush()
        try:
            # 等待模型、门控、容器及基线重算共用第一次起算的节点时限和累计尝试预算。
            async with asyncio.timeout(self.journal.config.node_timeout_seconds):
                commands = await self._commands(node)
                for _ in range(max_rounds):
                    record.rounds += 1
                    if await self._round(node, record, commands):
                        return
                raise NodeExecutionError("state revision conflict limit reached")
        except BaseException as exc:
            _record_failure(record, exc)
            raise
        finally:
            record.finished_at = time.time()
            self.journal.flush()

    async def _round(self, node: Node, record: NodeReceipt, commands: tuple[str, ...]) -> bool:
        base = None
        winner: Candidate | None = None
        try:
            version, base = await self.state.capture()
            winner = await self.race.run(node, record, commands, version, base)

            def committed(new_version: int) -> None:
                assert winner is not None
                winner.transferred = True
                winner.receipt.status = "published"
                record.status = "completed"
                record.published_version = new_version
                record.selected_candidate = winner.receipt.candidate_index
                self.journal.receipt.version = new_version
                self.journal.flush()

            if await self.state.publish(version, winner.snapshot, committed):
                return True
            # 兄弟节点已发布；旧成功结果保留为 superseded，在新基线上重跑原命令而非覆盖兄弟状态。
            winner.receipt.status = "superseded"
            self.journal.flush()
            return False
        finally:

            async def cleanup() -> None:
                try:
                    if winner is not None and not winner.transferred:
                        await winner.snapshot.close()
                finally:
                    if base is not None:
                        await base.close()

            await settle(asyncio.create_task(cleanup()))
