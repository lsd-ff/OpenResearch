from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from openresearch.errors import SandboxError
from openresearch.sandbox.core.models import CommandResult


class CheckerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_advice_attempts: int = Field(default=3, ge=1, le=3)
    advice_timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)


class WatchdogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_retry_window: int = Field(default=5, ge=1, le=100)
    command_timeout_seconds: float = Field(default=300, gt=0, le=3600, allow_inf_nan=False)
    checker: CheckerConfig = Field(default_factory=CheckerConfig)


class FailureKind(StrEnum):
    CIRCUIT_BROKEN = "CIRCUIT_BROKEN"
    TIMEOUT_HANG = "TIMEOUT_HANG"
    ENV_CORRUPTED = "ENV_CORRUPTED"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"


@dataclass(frozen=True)
class DiagnosisInput:
    command: str
    output: str
    exit_code: int | None
    feedback: str


@dataclass(frozen=True)
class CheckReport:
    succeeded: bool
    hint: str = ""
    advice_attempts: int = 0
    advice_errors: tuple[str, ...] = ()


class WatchdogError(SandboxError):
    def __init__(
        self,
        kind: FailureKind,
        command: str,
        hint: str,
        result: CommandResult | None = None,
        report: CheckReport | None = None,
    ) -> None:
        super().__init__(f"[{kind.value}] sandbox command failed")
        self.kind, self.command, self.hint = kind, command, hint
        self.result, self.report = result, report
