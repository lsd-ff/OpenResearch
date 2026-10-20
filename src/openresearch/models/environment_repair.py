from typing import Literal

from pydantic import Field

from openresearch.models.runtime import ContractModel


class EnvironmentRepairProposal(ContractModel):
    action: Literal["dependencies", "stop"]
    reason: str = Field(min_length=1, max_length=2000)
    requirements: list[str] = Field(default_factory=list, max_length=8)


class EnvironmentRepairDecision(ContractModel):
    before_sha256: str
    after_sha256: str | None = None
    proposal: EnvironmentRepairProposal | None = None
    accepted: bool = False
    error: str = ""
