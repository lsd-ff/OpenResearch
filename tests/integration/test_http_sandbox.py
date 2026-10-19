import httpx
import pytest

from openresearch.config import Settings
from openresearch.errors import SandboxError
from openresearch.sandbox.http import HttpSandbox


async def test_interrupted_stream_is_not_a_success(settings: Settings) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='{"type":"chunk","message":"partial"}\n')

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        adapter = HttpSandbox(settings, client)

        async def on_log(stream: str, message: str) -> None:
            pass

        with pytest.raises(SandboxError, match="without a final"):
            await adapter.execute("dk-test", "print(1)", on_log)


@pytest.mark.parametrize("status", [404, 405, 413, 500])
async def test_file_protocol_falls_back_only_when_bulk_upload_is_unsupported(settings, status):
    executed = []

    def handle(request):
        if request.url.path.endswith("/files"):
            return httpx.Response(status)
        executed.append(request.url.path)
        return httpx.Response(
            200,
            text='{"type":"final","response":{"stdout":"","stderr":"","exit_code":0}}\n',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://test")
    adapter = HttpSandbox(settings, client)
    try:
        if status in {404, 405}:
            await adapter.put_files("dk-test", {"input.txt": b"actual bytes"})
            assert executed == ["/api/v1/sandboxes/dk-test/python/stream"]
        else:
            with pytest.raises(httpx.HTTPStatusError):
                await adapter.put_files("dk-test", {"input.txt": b"actual bytes"})
            assert not executed
    finally:
        await adapter.shutdown()
