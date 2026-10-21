import asyncio
import sys
from contextlib import suppress

import pytest


@pytest.mark.parametrize("shutdown", ["input_open", "eof"])
async def test_real_input_monitor_does_not_abort_interpreter_shutdown(shutdown):
    program = (
        "import asyncio, sys\n"
        "from openresearch.distribution.application import stop_on_input_close\n"
        "async def main():\n"
        "    stopping = asyncio.Event()\n"
        "    stop_on_input_close(stopping)\n"
        "    await asyncio.sleep(0.1)\n"
        "    print('INPUT_MONITOR_READY', flush=True)\n"
        "    if sys.argv[1] == 'eof':\n"
        "        await asyncio.wait_for(stopping.wait(), 5)\n"
        "    else:\n"
        "        await asyncio.sleep(0.05)\n"
        "asyncio.run(main())\n"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-c",
        program,
        shutdown,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin and process.stdout and process.stderr
    try:
        ready = await asyncio.wait_for(process.stdout.readline(), 5)
        assert ready.rstrip(b"\r\n") == b"INPUT_MONITOR_READY"
        if shutdown == "eof":
            process.stdin.close()
        # 先等待解释器结束；communicate 会提前关闭 stdin，从而掩盖守护线程的缓冲锁崩溃。
        code = await asyncio.wait_for(process.wait(), 8)
        diagnostic = (await process.stderr.read()).decode(errors="replace")
        assert code == 0, diagnostic
        assert "Fatal Python error" not in diagnostic
    finally:
        process.stdin.close()
        if process.returncode is None:
            process.kill()
        await process.wait()
        with suppress(BrokenPipeError, ConnectionResetError):
            await process.stdin.wait_closed()
