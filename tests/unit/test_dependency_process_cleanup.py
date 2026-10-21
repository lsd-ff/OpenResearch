"""真实子进程验证管道超限/取消清理；程序仅模拟解析器输出，不运行实验。"""

import asyncio
import sys

import pytest

from openresearch.errors import ContractError
from openresearch.sources.dependency_resolution import DependencyResolver


@pytest.mark.parametrize("mode", ["overflow", "cancel_running", "cancel_creating"])
async def test_resolver_reaps_process_even_when_unread_pipe_buffers_are_full(
    monkeypatch, tmp_path, mode
):
    original = asyncio.create_subprocess_exec
    processes = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def create(*args, **kwargs):
        program = (
            "import sys, time\n"
            "sys.stdout.buffer.write(b'x' * 1048576)\nsys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        process = await original(
            sys.executable,
            "-u",
            "-c",
            program,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(process)
        started.set()
        if mode == "cancel_creating":
            await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    operation = asyncio.create_task(DependencyResolver()._compile("uv", tmp_path))
    try:
        await started.wait()
        if mode != "overflow":
            operation.cancel()
            release.set()
        async with asyncio.timeout(5):
            with pytest.raises(ContractError if mode == "overflow" else asyncio.CancelledError):
                await operation
        assert processes[0].returncode is not None
    finally:
        release.set()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        for process in processes:
            if process.returncode is None:
                process.kill()
            async with asyncio.timeout(5):
                await process.communicate()
