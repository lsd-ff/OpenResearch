"""PDF 专用网络边界：先解析并校验全部地址，随后只连接已校验 IP。"""

import asyncio
import base64
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from typing import cast

import httpcore
import httpx

from openresearch.errors import ContractError

Resolver = Callable[[str, int], Awaitable[list[str]]]


async def resolve_addresses(host: str, port: int) -> list[str]:
    try:
        entries = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise httpcore.ConnectError("cannot resolve remote PDF host") from None
    return list(dict.fromkeys(str(entry[4][0]) for entry in entries))


def public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return public_address(str(address.ipv4_mapped))
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        embedded = address.ipv4_mapped or address.sixtofour
        if address in ipaddress.IPv6Network("64:ff9b::/96"):
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        if embedded and not public_address(str(embedded)):
            return False
        if address.teredo and any(not public_address(str(ip)) for ip in address.teredo):
            return False
    return True


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        proxy: str = "",
        *,
        resolver: Resolver | None = None,
        network: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self.resolver = resolver or resolve_addresses
        self.network = network or httpcore.AnyIOBackend()
        try:
            self.proxy = httpx.Proxy(proxy) if proxy else None
            if self.proxy and (
                not self.proxy.url.host
                or (self.proxy.url.port is not None and not 1 <= self.proxy.url.port <= 65535)
            ):
                raise ValueError
        except (ValueError, httpx.InvalidURL):
            raise ContractError("remote PDF proxy configuration is invalid") from None
        if self.proxy and self.proxy.url.scheme not in {"http", "https"}:
            raise ContractError("remote PDF proxy must use HTTP or HTTPS")

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 -- httpcore 接口参数
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await self.resolver(host, port)
        if not addresses or any(not public_address(address) for address in addresses):
            raise ContractError("PDF URL resolves to a private, local or non-public address")
        for address in sorted(addresses, key=lambda value: ":" in value):
            try:
                if self.proxy:
                    return await self._tunnel(address, port, timeout)
                return await self.network.connect_tcp(
                    address, port, timeout, local_address, socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout):
                continue
        raise httpcore.ConnectError("cannot connect to public PDF destination")

    async def _tunnel(
        self, address: str, port: int, timeout_seconds: float | None
    ) -> httpcore.AsyncNetworkStream:
        assert self.proxy is not None
        proxy = self.proxy
        proxy_port = proxy.url.port or (443 if proxy.url.scheme == "https" else 80)
        stream = await self.network.connect_tcp(proxy.url.host, proxy_port, timeout_seconds)
        try:
            if proxy.url.scheme == "https":
                stream = await stream.start_tls(
                    httpcore.default_ssl_context(), proxy.url.host, timeout_seconds
                )
            origin = httpcore.Origin(proxy.url.raw_scheme, proxy.url.raw_host, proxy_port)
            target = (f"[{address}]:{port}" if ":" in address else f"{address}:{port}").encode()
            headers = [(b"Host", target), (b"Accept", b"*/*")]
            if proxy.raw_auth:
                headers.append(
                    (
                        b"Proxy-Authorization",
                        b"Basic " + base64.b64encode(b":".join(proxy.raw_auth)),
                    )
                )
            # CONNECT 的目标固定为公共 IP；后续 TLS 由原始域名完成，不能关闭证书校验。
            connection = httpcore.AsyncHTTP11Connection(origin, stream)
            response = await connection.handle_async_request(
                httpcore.Request(
                    "CONNECT",
                    httpcore.URL(
                        scheme=origin.scheme, host=origin.host, port=origin.port, target=target
                    ),
                    headers=headers,
                    extensions={"timeout": {"read": timeout_seconds, "write": timeout_seconds}},
                )
            )
            if not 200 <= response.status < 300:
                raise httpcore.ProxyError("remote PDF proxy refused the public-IP tunnel")
            return cast(httpcore.AsyncNetworkStream, response.extensions["network_stream"])
        except BaseException:
            await stream.aclose()
            raise

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 -- httpcore 接口参数
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("Unix sockets are not supported for remote PDF")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
