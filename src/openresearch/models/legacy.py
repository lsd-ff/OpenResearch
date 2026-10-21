import json
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from openresearch.models.research import EnvironmentReport
from openresearch.models.runtime import ContractModel
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec


class LegacyEnvironmentState(ContractModel):
    version: Literal["legacy.environment/v1"] = "legacy.environment/v1"
    lifecycle: Literal["recreate_per_execution"] = "recreate_per_execution"
    phase: Literal["probe", "locked", "installed"] = "locked"
    requests: list[str] = Field(default_factory=list, max_length=128)
    constraints: list[str] = Field(default_factory=list, max_length=128)
    spec: EnvironmentSpec
    lock: EnvironmentLock
    runtime: RuntimeBinding | None = None
    installation: EnvironmentReport | None = None

    @model_validator(mode="after")
    def actual_preparation(self) -> "LegacyEnvironmentState":
        if self.spec.interpreter != self.lock.interpreter:
            raise ValueError("prepared spec and lock must identify the same interpreter")
        if self.phase == "probe":
            if self.spec.requirements or self.lock.wheels or self.installation:
                raise ValueError("runtime probing cannot claim locked or installed dependencies")
            return self
        expected = dict(value.split("==", 1) for value in self.spec.requirements)
        locked = dict(wheel.requirement.split("==", 1) for wheel in self.lock.wheels)
        if expected != locked:
            raise ValueError("prepared requirements differ from their actual wheel lock")
        if (self.phase == "installed") != (self.installation is not None):
            raise ValueError("installed runtime requires an actual installation receipt")
        if self.installation and (
            self.installation.installed != expected
            or (
                self.lock.interpreter
                and self.installation.python != self.lock.interpreter.python_full_version
            )
        ):
            raise ValueError("installation receipt differs from the prepared environment")
        return self


class DirectExecutionRequest(ContractModel):
    task_id: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_.:-]*$")
    plan_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    task_name: str = Field(default="", max_length=256)
    task_type: str = Field(default="", max_length=64)
    task_description: str = Field(default="", max_length=16000)
    assigned_to: str = Field(default="", max_length=64)
    inputs: dict[str, Any] = Field(default_factory=dict, max_length=64)

    @field_validator("inputs")
    @classmethod
    def bounded_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 256 * 1024:
            raise ValueError("direct task inputs exceed 256 KiB")
        return value
