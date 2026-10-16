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
