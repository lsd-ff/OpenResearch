"""自然语言规划的提议与追问状态；实际资产和执行参数仍由服务端校验。"""

from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from openresearch.models.claims import RubricDraft
from openresearch.models.discovery import SearchResult
from openresearch.models.research import ExperimentVariant, ResearchSpec
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

    @field_validator("input_columns", mode="before")
    @classmethod
    def absent_input_columns(cls, value: Any) -> Any:
        # 模型的 null 表示未指定列；只归一化缺省值，不猜测任何真实列名。
        return [] if value is None else value


class PendingPlanning(ContractModel):
    intent: str = Field(min_length=1, max_length=16000)
    attachments: list[str] = Field(default_factory=list, max_length=8)
    repository_candidates: list[SearchResult] = Field(default_factory=list, max_length=10)
    paper_candidates: list[SearchResult] = Field(default_factory=list, max_length=10)
    question: str = Field(default="", max_length=4000)


class ExperimentDesign(ContractModel):
    entrypoint: str = Field(default="openresearch_run.py", pattern=r"\.py$")
    arguments: list[str] = Field(default_factory=list, max_length=16)
    patch_paths: list[Annotated[str, Field(pattern=r"\.py$")]] = Field(
        default_factory=lambda: ["openresearch_run.py"], max_length=3
    )
    dependencies: list[str] = Field(default_factory=list, max_length=32)
    ablations: list[ExperimentVariant] = Field(default_factory=list, max_length=3)
    rationale: str = Field(min_length=1, max_length=4000)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    rubric: RubricDraft = Field(default_factory=RubricDraft)

    @field_validator("entrypoint")
    @classmethod
    def valid_entrypoint(cls, value: str) -> str:
        return ResearchSpec.python_entrypoint(value)

    @field_validator("patch_paths")
    @classmethod
    def valid_patch_paths(cls, values: list[str]) -> list[str]:
        return ResearchSpec.bounded_python_paths(values)

    @model_validator(mode="after")
    def allow_adapter_entrypoint(self) -> "ExperimentDesign":
        # 自动新建入口本身就是已授权的适配路径；空列表不能阻断准备阶段。
        if self.entrypoint not in self.patch_paths:
            if len(self.patch_paths) == 3:
                raise ValueError("reserve the entrypoint within the three allowed Python paths")
            self.patch_paths = [*self.patch_paths, self.entrypoint]
        return self


class SourceSelection(ContractModel):
    choice: int | None = Field(default=None, ge=1, le=10)
    reason: str = Field(min_length=1, max_length=1000)


class RepositoryAssessment(ContractModel):
    covers_architecture: bool
    reason: str = Field(min_length=1, max_length=1000)
