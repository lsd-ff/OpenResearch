"""保留原始 Host/TLS 域名，用公开 httpcore 接口绑定 PDF 的实际连接地址。"""

from collections.abc import AsyncIterator

import httpcore
import httpx

from openresearch.sources.public_network import PublicNetworkBackend

CORE_ERRORS = (
    httpcore.NetworkError,
    httpcore.ProtocolError,
    httpcore.TimeoutException,
    httpcore.ProxyError,
    httpcore.UnsupportedProtocol,
)


class PdfBodyStream(httpx.AsyncByteStream):
    def __init__(self, response: httpcore.Response) -> None:
        self.response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.response.aiter_stream():
                yield chunk
        except CORE_ERRORS:
            raise httpx.ReadError("remote PDF body transport failed") from None

    async def aclose(self) -> None:
        await self.response.aclose()


class PdfTransport(httpx.AsyncBaseTransport):
    def __init__(self, backend: PublicNetworkBackend) -> None:
        self.pool = httpcore.AsyncConnectionPool(
            network_backend=backend,
            max_connections=2,
            max_keepalive_connections=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(request.stream, httpx.AsyncByteStream)
        try:
            response = await self.pool.handle_async_request(
                httpcore.Request(
                    request.method,
                    httpcore.URL(
                        scheme=request.url.raw_scheme,
                        host=request.url.raw_host,
                        port=request.url.port,
                        target=request.url.raw_path,
                    ),
                    headers=request.headers.raw,
                    content=request.stream,
                    extensions=request.extensions,
                )
            )
        except CORE_ERRORS:
            raise httpx.TransportError("remote PDF connection failed", request=request) from None
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=PdfBodyStream(response),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self.pool.aclose()
