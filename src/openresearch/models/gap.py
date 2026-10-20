from typing import Literal

from pydantic import Field, model_validator

from openresearch.models.audit import ReviewSnapshot
from openresearch.models.research import ResearchPatch
from openresearch.models.runtime import ContractModel


class GapProposal(ContractModel):
    status: Literal["patched", "not_justified", "insufficient_evidence"]
    diagnosis: str = Field(min_length=1, max_length=4000)
    patch: ResearchPatch = Field(default_factory=ResearchPatch)

    @model_validator(mode="after")
    def patch_matches_status(self) -> "GapProposal":
        if bool(self.patch.files) != (self.status == "patched"):
            raise ValueError("gap proposal status must match its actual patch")
        return self


class GapPatchIdentity(ContractModel):
    path: str
    before_sha256: str
    after_sha256: str


class GapDebugReport(ContractModel):
    version: Literal["research.gap-debug/v1"] = "research.gap-debug/v1"
    source: ReviewSnapshot
    status: Literal[
        "not_justified", "insufficient_evidence", "rejected", "rerun_completed", "rerun_failed"
    ] = "rejected"
    proposal: GapProposal | None = None
    patches: list[GapPatchIdentity] = Field(default_factory=list, max_length=6)
    execution_performed: bool = False
    preflight_performed: bool = False
    before_metrics: dict[str, dict[str, float]] = Field(default_factory=dict)
    after_metrics: dict[str, dict[str, float]] = Field(default_factory=dict)
    acceptance_summary: dict[str, int | float] = Field(default_factory=dict)
    error: str = ""
    scope: str = (
        "原实验保持不变；这里只报告补丁、实际预检/重跑和原冻结规则的判定。"
        "程序完成不表示结果差距已修复，也不证明论文完整效果。"
    )
