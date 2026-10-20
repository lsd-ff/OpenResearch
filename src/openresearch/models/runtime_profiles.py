import re
from typing import Literal, TypedDict

from pydantic import Field, field_validator

from openresearch.models.runtime import ContractModel

TargetPython = Literal["3.11", "3.12", "3.13"]


class RuntimeBinding(ContractModel):
    profile: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RuntimePreparationOptions(TypedDict, total=False):
    runtime: RuntimeBinding | None
    allow_runtime_select: bool


class RuntimeProfile(ContractModel):
    image: str = Field(min_length=1, max_length=256)
    python: TargetPython
    gpu: bool = False
    cpus: float = Field(default=1, ge=0.25, le=64, allow_inf_nan=False)
    memory_mb: int | None = Field(default=None, ge=64, le=131072)

    @field_validator("image")
    @classmethod
    def container_reference(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]*", value) or "//" in value:
            raise ValueError("runtime image must be a container image reference")
        if "@" in value and not re.search(r"@sha256:[a-f0-9]{64}$", value):
            raise ValueError("runtime image digest must use sha256")
        return value
