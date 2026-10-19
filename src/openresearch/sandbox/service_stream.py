import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from openresearch.config import Settings
from openresearch.sandbox.base import Sandbox


async def execution_stream(
    adapter: Sandbox, identifier: str, code: str, settings: Settings
) -> AsyncIterator[str]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=32)
    completed = False

    async def on_log(stream: str, message: str) -> None:
        await queue.put({"type": "chunk", "stream": stream, "message": message})

    async def produce() -> None:
        nonlocal completed
        try:
            async with asyncio.timeout(settings.task_timeout_seconds):
                result = await adapter.execute(identifier, code, on_log)
            completed = True
            await queue.put({"type": "final", "response": result.model_dump()})
        except Exception:
            await queue.put({"type": "error", "message": "sandbox execution failed"})
        finally:
            await queue.put(None)

    producer = asyncio.create_task(produce())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield json.dumps(event, ensure_ascii=False) + "\n"
        await producer
    finally:
        # 断线取消生产者时，不能让满队列的 finally.put 阻止容器清理。
        if not producer.done():
            producer.cancel()
            while not queue.empty():
                queue.get_nowait()
        with suppress(asyncio.CancelledError):
            await producer
        if not completed:
            async with asyncio.timeout(20):
                await asyncio.shield(adapter.close(identifier))
