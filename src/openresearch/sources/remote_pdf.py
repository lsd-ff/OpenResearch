import asyncio
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import httpx

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.sources.discovery import arxiv_pdf_url
from openresearch.sources.pdf_body import pdf_body
from openresearch.sources.pdf_transport import PdfTransport
from openresearch.sources.public_network import PublicNetworkBackend


def pdf_location(raw: str) -> httpx.URL:
    try:
        raw = raw.strip()
        parsed = urlsplit(raw)
        if (
            len(raw) > 4096
            or any(ord(char) < 32 for char in raw)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError
        location = httpx.URL(raw)
        if location.port is not None and not 1 <= location.port <= 65535:
            raise ValueError
        if location.host in {"arxiv.org", "export.arxiv.org"} and location.path.startswith("/abs/"):
            return httpx.URL(arxiv_pdf_url(str(location)))
        return location.copy_with(fragment=None)
    except (ValueError, httpx.InvalidURL):
        raise ContractError("PDF URL must be valid HTTP(S) without user information") from None


def pdf_filename(url: str) -> str:
    name = PurePosixPath(unquote(pdf_location(url).path)).name
    name = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)
    name = name[:150].strip(".") or "paper"
    return name if name.lower().endswith(".pdf") else name + ".pdf"


class RemotePdfReader:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def fetch(self, url: str) -> bytes:
        location = pdf_location(url)
        transport = self.transport or PdfTransport(
            PublicNetworkBackend(self.settings.source_proxy_url.get_secret_value())
        )
        try:
            async with asyncio.timeout(self.settings.source_download_timeout_seconds):
                async with httpx.AsyncClient(
                    transport=transport,
                    trust_env=False,
                    timeout=httpx.Timeout(15, connect=10),
                    follow_redirects=False,
                    headers={"User-Agent": "OpenResearch-Python/0.1", "Accept-Encoding": "gzip"},
                ) as client:
                    for attempt in range(3):
                        try:
                            return await self._follow(client, location)
                        except httpx.HTTPError as error:
                            transient = isinstance(error, httpx.TransportError) or (
                                isinstance(error, httpx.HTTPStatusError)
                                and error.response.status_code in {429, 502, 503, 504}
                            )
                            if not transient or attempt == 2:
                                raise
                            await asyncio.sleep(0.3 * 2**attempt)
        except (httpx.HTTPError, TimeoutError):
            # 错误消息不包含签名 URL、代理认证或第三方响应正文。
            raise ContractError("remote PDF source unavailable; retry or upload the PDF") from None
        raise AssertionError("PDF fetch did not return")

    async def _follow(self, client: httpx.AsyncClient, location: httpx.URL) -> bytes:
        for hop in range(6):
            client.cookies.clear()
            async with client.stream("GET", location) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    redirect = response.headers.get("location")
                    if not redirect or hop == 5:
                        raise ContractError("remote PDF redirect limit or location is invalid")
                    location = pdf_location(str(location.join(redirect)))
                    continue
                response.raise_for_status()
                if response.status_code != 200:
                    raise ContractError("remote PDF did not return HTTP 200")
                return await pdf_body(response, self.settings.upload_max_bytes)
        raise AssertionError("PDF redirect loop did not return")
