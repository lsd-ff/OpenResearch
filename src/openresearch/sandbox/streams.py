import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from openresearch.errors import SandboxError


async def stream_events(
    response: httpx.Response, max_bytes: int, *, sse: bool = False
) -> AsyncIterator[dict[str, Any]]:
    buffer = b""
    total = 0
    async for chunk in response.aiter_bytes(chunk_size=65536):
        total += len(chunk)
        if total > max_bytes:
            raise SandboxError("sandbox output exceeds configured size limit")
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            event = parse_event(line, sse)
            if event is not None:
                yield event
    if buffer.strip():
        event = parse_event(buffer, sse)
        if event is not None:
            yield event


def parse_event(line: bytes, sse: bool) -> dict[str, Any] | None:
    line = line.strip()
    if sse:
        if not line.startswith(b"data:"):
            return None
        line = line[5:].strip()
    if not line:
        return None
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError) as exc:
        raise SandboxError("invalid sandbox event") from exc
    if not isinstance(value, dict):
        raise SandboxError("invalid sandbox event")
    return value
