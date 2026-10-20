from typing import Literal

from pydantic import Field

from openresearch.models.environment_actions import EnvironmentRepairProposal
from openresearch.models.runtime import ContractModel
from openresearch.models.runtime_profiles import RuntimeBinding


class EnvironmentPreparationAttempt(ContractModel):
    number: int = Field(ge=1, le=3)
    phase: Literal["resolve", "lock"]
    requests: list[str] = Field(max_length=128)
    resolved_requirements: list[str] = Field(default_factory=list, max_length=128)
    exit_code: int | None = None
    diagnostic: str = Field(default="", max_length=16000)
    error: str = Field(default="", max_length=4000)
    runtime: RuntimeBinding | None = Field(default=None, exclude_if=lambda value: value is None)


class EnvironmentPreparationDecision(ContractModel):
    proposal: EnvironmentRepairProposal | None = None
    accepted: bool = False
    before_requests: list[str] = Field(max_length=128)
    after_requests: list[str] = Field(default_factory=list, max_length=128)
    error: str = Field(default="", max_length=4000)
    package_evidence: list[dict[str, str]] = Field(default_factory=list, max_length=4)
    runtime_before: RuntimeBinding | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    runtime_after: RuntimeBinding | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class EnvironmentPreparationReport(ContractModel):
    version: Literal["environment.preparation/v1"] = "environment.preparation/v1"
    success: bool = False
    constraints: list[str] = Field(default_factory=list, max_length=128)
    attempts: list[EnvironmentPreparationAttempt] = Field(default_factory=list, max_length=3)
    decisions: list[EnvironmentPreparationDecision] = Field(default_factory=list, max_length=2)
    runtime: RuntimeBinding | None = Field(default=None, exclude_if=lambda value: value is None)
