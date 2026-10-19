"""自然语言规划的提议与追问状态；实际资产和执行参数仍由服务端校验。"""

from typing import Literal

from pydantic import Field

from openresearch.models.discovery import SearchResult
from openresearch.models.research import ExperimentVariant
from openresearch.models.runtime import ContractModel


class IntentDraft(ContractModel):
    workflow: Literal["chat", "code", "paper", "reproduce", "benchmark", "data"]
    continue_pending: bool = True
    repository_url: str | None = None
    repository_ref: str | None = Field(default=None, max_length=128)
    paper_url: str | None = None
    paper_query: str = Field(default="", max_length=300)
    repository_query: str = Field(default="", max_length=300)
    input_columns: list[str] = Field(default_factory=list, max_length=16)
    target_column: str | None = None
    id_column: str | None = None
    task: Literal["classification", "regression"] = "classification"
    repository_choice: int | None = Field(default=None, ge=1, le=10)
    paper_choice: int | None = Field(default=None, ge=1, le=10)
    dataset_name: str | None = None


class PendingPlanning(ContractModel):
    intent: str = Field(min_length=1, max_length=16000)
    attachments: list[str] = Field(default_factory=list, max_length=8)
    repository_candidates: list[SearchResult] = Field(default_factory=list, max_length=10)
    paper_candidates: list[SearchResult] = Field(default_factory=list, max_length=10)
    question: str = Field(default="", max_length=4000)


class ExperimentDesign(ContractModel):
    entrypoint: str = "openresearch_run.py"
    arguments: list[str] = Field(default_factory=list, max_length=16)
    patch_paths: list[str] = Field(default_factory=lambda: ["openresearch_run.py"], max_length=3)
    dependencies: list[str] = Field(default_factory=list, max_length=32)
    ablations: list[ExperimentVariant] = Field(default_factory=list, max_length=3)
    rationale: str = Field(min_length=1, max_length=4000)
    limitations: list[str] = Field(default_factory=list, max_length=8)


class SourceSelection(ContractModel):
    choice: int | None = Field(default=None, ge=1, le=10)
    reason: str = Field(min_length=1, max_length=1000)
