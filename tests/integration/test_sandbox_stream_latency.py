import asyncio
import json
import threading
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from openresearch.sandbox.http import HttpSandbox
from openresearch.sandbox.streams import stream_events


class GatedStream(httpx.AsyncByteStream):
    def __init__(self, release: asyncio.Event, sse: bool) -> None:
        self.release, self.sse = release, sse

    async def __aiter__(self) -> AsyncIterator[bytes]:
        first = json.dumps({"type": "chunk", "message": "逐步输出"}, ensure_ascii=False).encode()
        if self.sse:
            first = b"data: " + first
        split = first.index("逐".encode()) + 1
        yield first[:split]
        yield first[split:] + b"\n"
        await self.release.wait()
        yield (b"data: " if self.sse else b"") + b'{"type":"final"}\n'


@pytest.mark.parametrize("sse", [False, True])
async def test_small_log_frame_arrives_before_the_producer_finishes(sse: bool) -> None:
    release = asyncio.Event()
    response = httpx.Response(200, stream=GatedStream(release, sse))
    events = stream_events(response, 1024, sse=sse)
    try:
        # 生产者尚未结束就必须交付完整小帧，同时覆盖 UTF-8 跨网络块拆分。
        async with asyncio.timeout(1):
            first = await anext(events)
        assert first["message"] == "逐步输出"
        assert not release.is_set()
        release.set()
        assert await anext(events) == {"type": "final"}
    finally:
        release.set()
        await events.aclose()
        await response.aclose()


async def test_real_http_callback_can_release_the_stream_before_its_final_response(settings):
    released = threading.Event()
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(b'{"type":"chunk","stream":"stdout","message":"FIRST"}\n')
            self.wfile.flush()
            # 只有客户端收到首帧才释放尾帧，不能以程序结束后的批量输出冒充实时流。
            ready = released.wait(2)
            observed.append(ready)
            final = {"type": "final", "response": {"stdout": "done", "stderr": "", "exit_code": 0}}
            self.wfile.write(json.dumps(final).encode() + b"\n")

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.sandbox_url = f"http://127.0.0.1:{server.server_port}"
    adapter = HttpSandbox(settings)

    async def on_log(stream: str, message: str) -> None:
        assert stream == "stdout" and message == "FIRST"
        released.set()

    try:
        result = await adapter.execute("dk-test", "unused protocol fixture", on_log)
        assert result.exit_code == 0 and result.stdout == "done"
        assert observed == [True]
    finally:
        released.set()
        await adapter.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
