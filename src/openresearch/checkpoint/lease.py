import asyncio
from types import TracebackType
from typing import TYPE_CHECKING

from openresearch.checkpoint.lifetime import ImageReference
from openresearch.checkpoint.registry import Snapshot
from openresearch.sandbox.core.models import CoreConfig
from openresearch.sandbox.core.transactions import settle

if TYPE_CHECKING:
    from openresearch.sandbox.core.session import CoreSandbox


class SnapshotLease:
    """必须显式关闭的快照租约；只在当前进程内授权隔离分支。"""

    def __init__(self, snapshot: Snapshot, config: CoreConfig, reference: ImageReference) -> None:
        self.snapshot, self.config = snapshot, config
        self._reference = reference

    def borrow(self) -> ImageReference:
        return self._reference.clone()

    def fork(self, config: CoreConfig | None = None) -> "CoreSandbox":
        from openresearch.sandbox.core.session import CoreSandbox

        # 分支始终使用租约的镜像；自定义配置只调整本地审计和资源约束。
        fork_config = (config or self.config).model_copy(update={"image": self.snapshot.image_id})
        return CoreSandbox(fork_config, snapshot=self)

    async def __aenter__(self) -> "SnapshotLease":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await settle(asyncio.create_task(asyncio.to_thread(self._reference.close)))
