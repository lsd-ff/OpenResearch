from openresearch.checkpoint.registry import RollbackReceipt, Snapshot, SnapshotRegistry
from openresearch.sandbox.core.engine import CoreEngine
from openresearch.sandbox.core.session import CoreSandbox


class CheckpointManager:
    def __init__(self, sandbox: CoreSandbox) -> None:
        self.sandbox = sandbox
        self.registry = SnapshotRegistry()

    async def commit(self, node_id: str) -> Snapshot:
        if not node_id.strip() or len(node_id) > 200 or "\x00" in node_id:
            raise ValueError("invalid checkpoint node id")

        def operation(engine: CoreEngine) -> Snapshot:
            snapshot = engine.commit(node_id)
            self.registry.register(snapshot)
            return snapshot

        return await self.sandbox.run_exclusive(operation)

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
        return receipt
