import httpx
import pytest

from openresearch.errors import ContractError
from openresearch.sources.http import read_bounded


@pytest.mark.parametrize("first", ["disconnect", 429, 503])
async def test_source_transient_failure_retries_actual_get(first):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            if first == "disconnect":
                raise httpx.ConnectError("disconnected", request=request)
            return httpx.Response(first)
        return httpx.Response(200, content=b"real source")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await read_bounded(client, "https://api.github.com/source", 20) == b"real source"
    assert calls == 2


@pytest.mark.parametrize("failure", [404, "oversize", "disconnect"])
async def test_source_retry_keeps_size_policy_and_attempt_bound(failure):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if failure == "disconnect":
            raise httpx.ConnectError("disconnected", request=request)
        return (
            httpx.Response(200, content=b"too many bytes")
            if failure == "oversize"
            else httpx.Response(404)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ContractError):
            await read_bounded(client, "https://api.github.com/source", 4)
    assert calls == (3 if failure == "disconnect" else 1)
