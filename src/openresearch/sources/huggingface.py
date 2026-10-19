"""HuggingFace Papers（原 Go 工程的 Papers with Code 来源）契约适配。"""

import json
import re
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field, ValidationError

from openresearch.errors import ContractError
from openresearch.models.discovery import SearchRequest, SearchResult
from openresearch.models.sources import RepositorySpec
from openresearch.sources.http import read_bounded

BASE_URL = "https://huggingface.co/api/papers"


class PaperMetadata(BaseModel):
    id: str
    title: str
    summary: str = ""
    githubRepo: str | None = None

    @property
    def paper_url(self) -> str:
        if not re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", self.id):
            raise ContractError("HuggingFace returned an invalid arXiv identifier")
        return "https://arxiv.org/abs/" + self.id


class PaperHit(BaseModel):
    paper: PaperMetadata


class PaperSearch(BaseModel):
    hits: list[PaperHit] = Field(max_length=100)


async def search_papers(client: httpx.AsyncClient, request: SearchRequest) -> list[SearchResult]:
    try:
        raw = await read_bounded(
            client, BASE_URL + "/search?" + urlencode({"q": request.query}), 2 * 1024 * 1024
        )
        hits = PaperSearch(hits=json.loads(raw)).hits[: request.limit]
        results: dict[str, SearchResult] = {}
        for hit in hits:
            paper = hit.paper
            paper_url = paper.paper_url
            if request.kind == "paper":
                results[paper_url] = SearchResult(
                    title=paper.title,
                    url=paper_url,
                    pdf_url=paper_url.replace("/abs/", "/pdf/"),
                    paper_url=paper_url,
                    description=paper.summary[:4000],
                    source="huggingface",
                )
                continue
            if paper.githubRepo is None:
                # 搜索结果可能省略仓库；只向固定 API 查询详情，不扫描任意来源链接。
                detail = await read_bounded(client, BASE_URL + "/" + paper.id, 512000)
                paper = PaperMetadata.model_validate_json(detail)
                if paper.paper_url != paper_url:
                    raise ContractError("HuggingFace paper detail identity changed")
            if not paper.githubRepo:
                continue
            try:
                spec = RepositorySpec(url=paper.githubRepo)
            except ValueError:
                continue
            results[spec.url] = SearchResult(
                title=paper.title + " — " + spec.slug,
                url=spec.url,
                paper_url=paper_url,
                description=paper.summary[:4000],
                source="huggingface",
            )
        return list(results.values())[: request.limit]
    except (ValidationError, json.JSONDecodeError) as exc:
        raise ContractError("invalid HuggingFace Papers response") from exc
