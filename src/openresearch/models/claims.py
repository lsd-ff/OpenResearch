import math
from typing import Literal

from pydantic import Field, field_validator, model_validator

from openresearch.models.chat import SourceCitation
from openresearch.models.runtime import ContractModel

VerdictStatus = Literal[
    "verified", "partially_reproduced", "contradicted", "unverifiable", "blocked_by_missing_asset"
]


class CriterionDraft(ContractModel):
    description: str = Field(min_length=1, max_length=1000)
    metric_name: str | None = None
    expected_value: float | None = None
    tolerance: float = Field(default=0.0, ge=0)
    operator: Literal["within", "at_least", "at_most"] = "within"
    variant: str = "baseline"
    required_evidence: list[str] = Field(default=["research_report"], max_length=5)

    @field_validator("expected_value", "tolerance")
    @classmethod
    def finite_values(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("rubric values must be finite")
        return value

    @model_validator(mode="after")
    def numeric_pair(self) -> "CriterionDraft":
        if (self.metric_name is None) != (self.expected_value is None):
            raise ValueError("numeric criterion requires both metric and expected value")
        return self


class ClaimDraft(ContractModel):
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=2000)
    source: SourceCitation | None = None
    criteria: list[CriterionDraft] = Field(min_length=1, max_length=8)


class RubricDraft(ContractModel):
    paper_title: str = Field(default="User-defined experiment", max_length=500)
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=16)


class FrozenCriterion(CriterionDraft):
    id: str


class FrozenClaim(ContractModel):
    id: str
    title: str
    statement: str
    source_locator: str
    source_sha256: str | None
    claim_type: Literal["quantitative", "semantic"]
    importance: float = 1.0
    criteria: list[FrozenCriterion]


class FrozenRubric(ContractModel):
    version: Literal["claim.rubric/v1"] = "claim.rubric/v1"
    paper_title: str
    source_artifact: str
    claims: list[FrozenClaim]
    sha256: str


class SemanticFinding(ContractModel):
    criterion_id: str
    status: VerdictStatus
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    observed_value: str = Field(default="", max_length=2000)
    evidence_key: str
    quote: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=2000)


class SemanticAssessment(ContractModel):
    findings: list[SemanticFinding] = Field(max_length=128)


class CriterionVerdict(ContractModel):
    criterion_id: str
    description: str
    status: VerdictStatus
    confidence: float = Field(ge=0, le=1)
    observed_value: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str


class ClaimResult(ContractModel):
    claim_id: str
    title: str
    statement: str
    source_locator: str
    claim_type: str
    status: VerdictStatus
    confidence: float
    criteria: list[CriterionVerdict]


class EvidenceNode(ContractModel):
    id: str
    artifact_key: str
    evidence_type: str
    sha256: str
    available: bool = True
    summary: str


class ClaimEvidenceGraph(ContractModel):
    version: Literal["claim.evidence/v1"] = "claim.evidence/v1"
    status: str = "assessed"
    status_reason: str
    rubric_sha256: str
    evidence: list[EvidenceNode]
    claims: list[ClaimResult]
    summary: dict[str, int | float]
