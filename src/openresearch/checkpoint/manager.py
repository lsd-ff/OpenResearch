from openresearch.checkpoint.feedback import FeedbackHistory
from openresearch.checkpoint.lease import SnapshotLease
from openresearch.checkpoint.registry import RollbackReceipt, Snapshot, SnapshotRegistry
from openresearch.sandbox.core.engine import CoreEngine
from openresearch.sandbox.core.session import CoreSandbox


class CheckpointManager:
    def __init__(self, sandbox: CoreSandbox) -> None:
        self.sandbox = sandbox
        self.registry = SnapshotRegistry()
        self.feedback = FeedbackHistory()

    async def commit(self, node_id: str) -> Snapshot:
        self._validate_node_id(node_id)

        def operation(engine: CoreEngine) -> Snapshot:
            return self._commit(engine, node_id)

        return await self.sandbox.run_exclusive(operation)

    @staticmethod
    def _validate_node_id(node_id: str) -> None:
        if not node_id.strip() or len(node_id) > 200 or "\x00" in node_id:
            raise ValueError("invalid checkpoint node id")

    def _commit(self, engine: CoreEngine, node_id: str) -> Snapshot:
        snapshot = engine.commit(node_id)
        self.registry.register(snapshot)
        return snapshot

    async def pin(self, node_id: str) -> SnapshotLease:
        return await self._pin(node_id, commit=False)

    async def commit_and_pin(self, node_id: str) -> SnapshotLease:
        self._validate_node_id(node_id)
        return await self._pin(node_id, commit=True)

    async def _pin(self, node_id: str, *, commit: bool) -> SnapshotLease:
        lease: SnapshotLease | None = None

        def operation(engine: CoreEngine) -> SnapshotLease:
            nonlocal lease
            snapshot = self._commit(engine, node_id) if commit else self.registry.get(node_id)
            lease = engine.pin(snapshot)
            return lease

        try:
            return await self.sandbox.run_exclusive(operation)
        except BaseException:
            # 线程事务排空后，调用者可能已取消。未交付出去的租约必须由这里归还。
            if lease is not None:
                await lease.close()
            raise

    async def rollback_to_node(self, node_id: str) -> RollbackReceipt:
        def operation(engine: CoreEngine) -> RollbackReceipt:
            return self._restore(engine, self.registry.get(node_id))

        return await self.sandbox.run_exclusive(operation)

    async def rollback_to_latest(self) -> RollbackReceipt:
        return await self.sandbox.run_exclusive(
            lambda engine: self._restore(engine, self.registry.latest())
        )

    async def rollback(self, image_id: str) -> RollbackReceipt:
        def operation(engine: CoreEngine) -> RollbackReceipt:
            snapshot = next(
                (item for item in self.registry.all() if item.image_id == image_id), None
            )
            if snapshot is None:
                raise KeyError("checkpoint image is not in this registry")
            return self._restore(engine, snapshot)

        return await self.sandbox.run_exclusive(operation)

    def _restore(self, engine: CoreEngine, snapshot: Snapshot) -> RollbackReceipt:
        receipt = engine.restore(snapshot)
        self.registry.restore(snapshot.node_id)
        self.feedback.record_restoration(receipt)
        return receipt

    def record_failure_and_get_feedback(self, node_id: str, command: str, output: str) -> str:
        self.feedback.record_failure(node_id, command, output)
        return self.feedback.context()

    def get_full_feedback_context(self) -> str:
        return self.feedback.context()
