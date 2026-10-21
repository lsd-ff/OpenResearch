from typing import Literal

from pydantic import Field, model_validator

from openresearch.models.discovery import SearchRequest, SearchResult
from openresearch.models.runtime import ContractModel
from openresearch.models.sources import RepositorySpec


class RepositoryDiscoveryRequest(ContractModel):
    search: SearchRequest | None = None
    preferred: RepositorySpec | None = None
    ref: str = Field(default="HEAD", min_length=1, max_length=128)

    @model_validator(mode="after")
    def repository_queries_only(self) -> "RepositoryDiscoveryRequest":
        if self.search and (self.search.kind != "repository" or self.search.provider == "arxiv"):
            raise ValueError("repository discovery requires repository search")
        RepositorySpec.safe_ref(self.ref)
        if self.preferred and self.preferred.ref != self.ref:
            raise ValueError("preferred repository differs from the requested reference")
        return self


class RepositoryCandidate(ContractModel):
    repository: RepositorySpec
    title: str = Field(max_length=500)
    description: str = Field(default="", max_length=4000)
    origin: Literal["user_preference", "paper_link", "github", "huggingface", "curated_fallback"]
    search_result: SearchResult | None = None
    upload_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    fallback_used: bool = False
    score: int = Field(default=0, ge=0)
    matching_terms: int = Field(default=0, ge=0)


class RepositoryDiscoveryReport(ContractModel):
    version: Literal["repository.discovery/v1"] = "repository.discovery/v1"
    status: Literal["selected", "ambiguous", "not_found", "unavailable"]
    candidates: list[RepositoryCandidate] = Field(default_factory=list, max_length=10)
    queries: list[SearchRequest] = Field(default_factory=list, max_length=2)
    selected: RepositorySpec | None = None
    selection_basis: str = Field(default="", max_length=1000)
    errors: list[str] = Field(default_factory=list, max_length=2)
    clone_verified: Literal[False] = False
    scope: str = "仅保存实际候选、来源及选择依据；下载、固定提交和代码执行须由后续阶段验证。"

    @model_validator(mode="after")
    def selected_candidate_exists(self) -> "RepositoryDiscoveryReport":
        if (self.status == "selected") != (self.selected is not None):
            raise ValueError("discovery status differs from the actual selection")
        if self.selected and self.selected not in [item.repository for item in self.candidates]:
            raise ValueError("selected repository is absent from the actual candidates")
        return self
