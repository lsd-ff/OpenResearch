import asyncio

import httpx

from openresearch.errors import ContractError


async def read_bounded(
    client: httpx.AsyncClient, url: str, limit: int, *, timeout_seconds: float = 60
) -> bytes:
    """只读来源获取可重试瞬断；三次尝试共用期限，大小限制不重试。"""
    try:
        async with asyncio.timeout(timeout_seconds):
            for attempt in range(3):
                try:
                    return await _read_once(client, url, limit)
                except httpx.HTTPError as exc:
                    transient = isinstance(exc, httpx.TransportError) or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code in {429, 502, 503, 504}
                    )
                    if not transient or attempt == 2:
                        raise
                    await asyncio.sleep(0.3 * 2**attempt)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise ContractError("external source unavailable; retry or provide another source") from exc
    raise AssertionError("bounded source loop did not return")


async def _read_once(client: httpx.AsyncClient, url: str, limit: int) -> bytes:
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        output = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            if len(output) + len(chunk) > limit:
                raise ContractError("external source exceeds configured size limit")
            output.extend(chunk)
        return bytes(output)
