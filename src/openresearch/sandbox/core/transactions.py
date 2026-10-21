import asyncio
from collections.abc import Callable


async def settle[T](task: asyncio.Task[T], cancel: Callable[[], None] | None = None) -> T:
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if cancel:
            cancel()
        # 已进入 Docker/TTY 的后台线程必须排空，不能在取消后越过事务锁修改状态。
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
