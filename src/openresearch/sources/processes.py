import asyncio
from contextlib import suppress

from openresearch.errors import ContractError


async def discard(stream: asyncio.StreamReader | None) -> None:
    if stream is not None:
        while await stream.read(4096):
            pass


async def _close_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    try:
        # Windows 的 wait() 还等待管道关闭。原读者被取消或超限后须继续丢弃缓冲，
        # 否则暂停读取的管道会让已退出子进程的清理永久等待；不累计清理输出。
        async with asyncio.timeout(10):
            await asyncio.gather(discard(process.stdout), discard(process.stderr), process.wait())
    except TimeoutError:
        raise ContractError("dependency resolver process cleanup timed out") from None


async def close_process(process: asyncio.subprocess.Process) -> None:
    cleanup = asyncio.create_task(_close_process(process))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await cleanup
        raise
