from typing import Annotated, Literal

from packaging.requirements import InvalidRequirement, Requirement
from pydantic import Field, field_validator, model_validator

from openresearch.models.research import BenchmarkSpec
from openresearch.models.runtime import ContractModel
from openresearch.models.sources import PackageDescription


class FrameworkTarget(ContractModel):
    name: str = Field(min_length=1, max_length=80)
    requirements: list[str] = Field(default_factory=list, max_length=4)
    import_roots: list[Annotated[str, Field(pattern=r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")]] = Field(
        default_factory=list,
        max_length=8,
    )

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("framework name must be nonempty")
        return value.strip()

    @field_validator("requirements")
    @classmethod
    def public_requirements(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                requirement = Requirement(value)
            except InvalidRequirement as exc:
                raise ValueError("invalid framework requirement") from exc
            if requirement.url or requirement.extras or requirement.marker:
                raise ValueError(
                    "framework dependencies must use public package names/version ranges"
                )
        return values


class ComparisonSpec(ContractModel):
    targets: list[FrameworkTarget] = Field(min_length=2, max_length=4)
    mode: Literal["research", "benchmark"] = "benchmark"
    benchmark: BenchmarkSpec | None = None
    answer_mode: Literal["auto", "rag", "none"] = "auto"
    answer_column: str | None = None

    @model_validator(mode="after")
    def distinct_targets(self) -> "ComparisonSpec":
        names = [
            target.name.casefold().replace("-", "").replace(" ", "") for target in self.targets
        ]
        if len(set(names)) != len(names):
            raise ValueError("comparison requires distinct frameworks")
        if self.mode == "research" and self.benchmark:
            raise ValueError("research comparison must not claim a dataset was evaluated")
        if (
            self.answer_column
            and self.benchmark
            and self.answer_column in self.benchmark.input_columns
        ):
            raise ValueError("reference answers must be withheld from predictor inputs")
        return self

    @property
    def needs_answers(self) -> bool:
        if self.mode != "benchmark" or self.answer_mode == "none":
            return False
        return (
            self.answer_mode == "rag"
            or self.benchmark is None
            or {"query", "documents"}.issubset(self.benchmark.input_columns)
        )


class ComparisonProtocol(ContractModel):
    version: Literal["comparison.protocol/v1"] = "comparison.protocol/v1"
    mode: Literal["research", "benchmark"]
    origin: Literal["none", "synthetic", "uploaded"]
    benchmark: BenchmarkSpec | None = None
    dataset_sha256: str | None = None
    sample_count: int = 0
    answer_column: str | None = None
    scope: str


class FrameworkSource(ContractModel):
    packages: list[PackageDescription]
    import_roots: list[str]
    api_excerpts: dict[str, str] = Field(default_factory=dict)


class FrameworkEvidence(ContractModel):
    package: str
    field: Literal["summary", "description"]
    quote: str = Field(min_length=1, max_length=1000)


class FrameworkFit(ContractModel):
    response: str = Field(min_length=1, max_length=8000)
    evidence: list[FrameworkEvidence] = Field(min_length=1, max_length=8)


class FrameworkAssessment(ContractModel):
    response: str = Field(min_length=1, max_length=8000)
    source_ids: list[Annotated[int, Field(ge=0)]] = Field(min_length=1, max_length=8)


class FrameworkResearchReport(ContractModel):
    name: str
    packages: list[PackageDescription]
    fit: FrameworkFit | None = None
    error: str = ""


class GeneratedAnswer(ContractModel):
    id: str = Field(min_length=1, max_length=256)
    answer: str = Field(max_length=2000)


class GeneratedAnswers(ContractModel):
    answers: list[GeneratedAnswer] = Field(max_length=2000)


class FrameworkAnswerReport(ContractModel):
    version: Literal["framework.answers/v1"] = "framework.answers/v1"
    answers: list[GeneratedAnswer]
    generation_seconds: float = Field(ge=0)
    metrics: dict[str, float] = Field(default_factory=dict)
    model: str
    scope: str = "Real shared generation model following actual framework retrieval."
