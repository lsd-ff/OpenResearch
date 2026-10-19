from typing import Literal

from pydantic import Field

from openresearch.models.runtime import ContractModel


class SearchRequest(ContractModel):
    kind: Literal["paper", "repository"]
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=10)
    provider: Literal["auto", "huggingface", "arxiv", "github"] = "auto"


class SearchResult(ContractModel):
    title: str
    url: str
    description: str
    source: Literal["arxiv", "github", "huggingface"]
    authors: list[str] = Field(default_factory=list)
    published: str | None = None
    pdf_url: str | None = None
    paper_url: str | None = None
    fallback_used: bool = False
