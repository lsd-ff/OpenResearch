from typing import Literal

from pydantic import Field

from openresearch.models.environment_preparation import EnvironmentPreparationReport
from openresearch.models.research import EnvironmentReport
from openresearch.models.runtime import ContractModel, ExecutionResult
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.models.sources import EnvironmentLock


class RecoveryProposal(ContractModel):
    action: Literal["code", "dependencies", "stop"]
    reason: str = Field(min_length=1, max_length=2000)
    code: str | None = Field(default=None, max_length=131072)
    requirements: list[str] = Field(default_factory=list, max_length=8)


class RecoveryDecision(ContractModel):
    proposal: RecoveryProposal | None = None
    accepted: bool
    error: str = ""
    preparation: EnvironmentPreparationReport | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class CodeAttempt(ContractModel):
    number: int = Field(ge=1, le=3)
    phase: Literal["syntax", "syntax_pending", "install", "execute", "outputs"]
    code: str = Field(max_length=131072)
    code_sha256: str
    output: ExecutionResult | None = None
    output_truncated: bool = False
    execution_requested: bool = False
    sandbox_id: str | None = None
    container_image_id: str | None = None
    environment_lock: EnvironmentLock = Field(default_factory=EnvironmentLock)
    environment: EnvironmentReport | None = None
    error: str = ""
    runtime: RuntimeBinding | None = Field(default=None, exclude_if=lambda value: value is None)


class CodeRecoveryReport(ContractModel):
    version: Literal["code.recovery/v1"] = "code.recovery/v1"
    success: bool = False
    max_repairs: int = Field(ge=0, le=2)
    attempts: list[CodeAttempt] = Field(default_factory=list, max_length=3)
    decisions: list[RecoveryDecision] = Field(default_factory=list, max_length=2)
    original_code_sha256: str
    final_code_sha256: str = ""
    error: str = ""
    runtime: RuntimeBinding | None = Field(default=None, exclude_if=lambda value: value is None)
    syntax_validation: Literal["host_ast", "deferred_to_sandbox"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
