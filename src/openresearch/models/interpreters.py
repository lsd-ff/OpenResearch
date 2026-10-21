from typing import Literal, cast

from pydantic import ConfigDict, Field, field_validator

from openresearch.models.runtime import ContractModel
from openresearch.models.runtime_profiles import TargetPython


class InterpreterIdentity(ContractModel):
    model_config = ConfigDict(frozen=True)

    python_full_version: str = Field(pattern=r"^3\.(?:11|12|13)\.(?:0|[1-9][0-9]{0,3})$")
    implementation: Literal["CPython"]
    system: Literal["Linux"]
    machine: Literal["x86_64"]
    libc: Literal["glibc"]
    libc_version: str = Field(pattern=r"^[0-9]{1,3}\.[0-9]{1,3}$")
    container_image_id: str | None = Field(
        default=None, pattern=r"^sha256:[a-f0-9]{64}$", exclude_if=lambda value: value is None
    )

    @field_validator("libc_version")
    @classmethod
    def supported_libc(cls, value: str) -> str:
        if tuple(int(part) for part in value.split(".")) < (2, 36):
            raise ValueError("runtime requires glibc >= 2.36")
        return value

    @property
    def python(self) -> TargetPython:
        return cast(TargetPython, self.python_full_version.rsplit(".", 1)[0])
