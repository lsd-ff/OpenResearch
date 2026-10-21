import asyncio
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urlsplit

import httpx

from openresearch.errors import ContractError
from openresearch.models.discovery import SearchRequest, SearchResult
from openresearch.sources.http import read_bounded
from openresearch.sources.huggingface import search_papers

ATOM = "{http://www.w3.org/2005/Atom}"
logger = logging.getLogger(__name__)


def arxiv_pdf_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc not in {"arxiv.org", "export.arxiv.org"}
        or parts.query
        or parts.fragment
    ):
        raise ContractError(
            "remote PDF reader supports HTTPS arXiv URLs; upload other PDFs directly"
        )
    match = re.fullmatch(
        r"/(?:pdf|abs)/((?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)(?:\.pdf)?", parts.path
    )
    if match is None:
        raise ContractError("invalid arXiv paper URL")
    return "https://arxiv.org/pdf/" + match[1]


def parse_arxiv(raw: bytes, limit: int) -> list[SearchResult]:
    # 响应体已受限；拒绝 DTD，避免把外部实体或扩展声明交给 XML 解析器。
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ContractError("invalid arXiv response")
    try:
        entries = ET.fromstring(raw).findall(f"{ATOM}entry")[:limit]
    except ET.ParseError as exc:
        raise ContractError("invalid arXiv response") from exc
    result = []
    for entry in entries:
        url = (entry.findtext(f"{ATOM}id") or "").replace("http://", "https://", 1)
        pdf = arxiv_pdf_url(url)
        result.append(
            SearchResult(
                title=" ".join((entry.findtext(f"{ATOM}title") or "").split()),
                description=" ".join((entry.findtext(f"{ATOM}summary") or "").split())[:4000],
                url=url,
                pdf_url=pdf,
                source="arxiv",
                published=entry.findtext(f"{ATOM}published"),
                authors=[
                    author.findtext(f"{ATOM}name") or ""
                    for author in entry.findall(f"{ATOM}author")
                ],
            )
        )
    return result


class SourceDiscovery:
    def __init__(
        self, transport: httpx.AsyncBaseTransport | None = None, *, proxy: str | None = None
    ) -> None:
        self.transport = transport
        self.proxy = proxy
        self.lock = asyncio.Lock()
        self.last_request = 0.0

    async def search(self, request: SearchRequest) -> list[SearchResult]:
        async with self.lock:
            delay = 6 - (time.monotonic() - self.last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self.last_request = time.monotonic()
            async with httpx.AsyncClient(
                timeout=10,
                trust_env=True,
                proxy=self.proxy,
                transport=self.transport,
                headers={"User-Agent": "OpenResearch-Python"},
            ) as client:
                return await self._providers(client, request)

    async def _providers(
        self, client: httpx.AsyncClient, request: SearchRequest
    ) -> list[SearchResult]:
        if request.provider == "huggingface":
            return await search_papers(client, request)
        if request.provider != "auto":
            expected = "arxiv" if request.kind == "paper" else "github"
            if request.provider != expected:
                raise ContractError("source provider does not support the requested search kind")
            return await self._search(client, request)
        available = False
        try:
            # 整个主来源有总期限，详情较多或来源限流也不能阻塞 GitHub/arXiv 回退。
            async with asyncio.timeout(15):
                results = await search_papers(client, request)
            available = True
            if results:
                return results
        except (ContractError, TimeoutError):
            logger.info("HuggingFace Papers unavailable; trying fallback source")
        try:
            results = await self._search(client, request)
        except ContractError:
            if available:
                return []
            raise
        for result in results:
            result.fallback_used = True
        return results

    async def _search(
        self, client: httpx.AsyncClient, request: SearchRequest
    ) -> list[SearchResult]:
        if request.kind == "paper":
            url = "https://export.arxiv.org/api/query?" + urlencode(
                {"search_query": request.query, "start": 0, "max_results": request.limit}
            )
            return parse_arxiv(await read_bounded(client, url, 2 * 1024 * 1024), request.limit)
        url = "https://api.github.com/search/repositories?" + urlencode(
            {"q": request.query, "per_page": request.limit}
        )
        try:
            response = json.loads(await read_bounded(client, url, 2 * 1024 * 1024))
            items = response["items"][: request.limit]
            return [
                SearchResult(
                    title=item["full_name"],
                    url=item["html_url"],
                    source="github",
                    description=item.get("description") or "",
                    stars=item.get("stargazers_count"),
                    archived=item.get("archived"),
                    fork=item.get("fork"),
                )
                for item in items
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise ContractError("invalid GitHub search response") from exc
