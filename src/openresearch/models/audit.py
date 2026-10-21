from typing import Literal

from pydantic import Field

from openresearch.models.runtime import ContractModel, Status

AUDIT_ROLES = {
    "benchmark_validate": {"research_coding_agent", "data_agent"},
    "verify_result": {"data_agent"},
    "paper_compare": {"data_agent"},
    "claim_evidence_build": {"data_agent"},
    "result_visualization": {"data_agent"},
    "render_plot": {"data_agent"},
}


class ArtifactPin(ContractModel):
    plan_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewSnapshot(ContractModel):
    version: Literal["result.review-input/v1"] = "result.review-input/v1"
    source_plan_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    source_task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    source_run_count: int = Field(ge=0)
    source_contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_status: Status
    artifacts: dict[str, ArtifactPin] = Field(max_length=128)
    supplements: dict[str, ArtifactPin] = Field(default_factory=dict, max_length=8)


class ResultAudit(ContractModel):
    version: Literal["result.audit/v1"] = "result.audit/v1"
    source_plan_id: str
    source_task_id: str
    source_status: Status
    kind: Literal["research", "code", "data", "unavailable"] = "unavailable"
    status: Literal["verified", "failed", "unverifiable"] = "unverifiable"
    integrity_valid: bool = True
    execution_success: bool | None = None
    sample_count: int | None = None
    dataset_sha256: str | None = None
    metrics: dict[str, dict[str, float]] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    input_sha256: dict[str, str] = Field(default_factory=dict)
    scope: str = "仅核对原运行的实际数据、产物与回执，不执行新实验，不证明泛化或论文完整效果。"
