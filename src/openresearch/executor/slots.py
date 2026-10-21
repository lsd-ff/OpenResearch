from openresearch.checkpoint.lease import SnapshotLease
from openresearch.executor.gates import Gatekeeper
from openresearch.executor.models import Node
from openresearch.sandbox.core.models import CoreConfig
from openresearch.sandbox.core.session import CoreSandbox


class SandboxSlot:
    def __init__(
        self, sandbox: CoreSandbox, gates: Gatekeeper, io_weight: int, gpu_weight: int
    ) -> None:
        self.sandbox = sandbox
        self._reservation = gates.reserve(io_weight, gpu_weight)
        self._acquired = False

    async def start(self) -> None:
        await self._reservation.__aenter__()
        self._acquired = True
        await self.sandbox.__aenter__()

    async def close(self) -> None:
        # 实际容器清理成功后才归还配额；删除失败不能唤醒超出真实容量的新候选。
        await self.sandbox.close()
        if self._acquired:
            await self._reservation.__aexit__(None, None, None)
            self._acquired = False


class CandidateSlot(SandboxSlot):
    def __init__(
        self, base: SnapshotLease, config: CoreConfig, gates: Gatekeeper, node: Node
    ) -> None:
        super().__init__(base.fork(config), gates, node.io_weight, node.gpu_weight)
