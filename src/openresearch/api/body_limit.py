import re

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyLimitMiddleware:
    """在 multipart 落盘前限制整个请求体，同时覆盖无 Content-Length 的上传。"""

    def __init__(
        self, app: ASGIApp, max_bytes: int, path_limits: dict[str, int] | None = None
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path_limits = path_limits or {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        limit = next(
            (
                size
                for pattern, size in self.path_limits.items()
                if re.fullmatch(pattern, scope["path"])
            ),
            self.max_bytes,
        )
        declared = headers.get(b"content-length", b"0")
        if not declared.isdigit() or int(declared) > limit:
            await JSONResponse({"error": "request body exceeds configured limit"}, 413)(
                scope, receive, send
            )
            return
        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise HTTPException(413, "request body exceeds configured limit")
            return message

        await self.app(scope, bounded_receive, send)
