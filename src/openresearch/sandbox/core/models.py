import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data_dir: Path = Path(".data/docker-core")
    image: str = Field(default="ubuntu:22.04", min_length=1, max_length=256)
    runtime: str = Field(default="runc", pattern=r"^[A-Za-z0-9_.-]+$", max_length=64)
    working_dir: str = "/workspace"
    network: Literal["none", "bridge"] = "none"
    memory_bytes: int = Field(default=2 * 1024**3, ge=64 * 1024**2, le=64 * 1024**3)
    cpus: float = Field(default=2, gt=0, le=64, allow_inf_nan=False)
    timeout_seconds: float = Field(default=300, gt=0, le=3600, allow_inf_nan=False)
    max_command_bytes: int = Field(default=65536, ge=1024, le=1024**2)
    max_output_bytes: int = Field(default=8 * 1024**2, ge=1024, le=64 * 1024**2)
    keep_first_lines: int = Field(default=20, ge=0, le=1000)
    keep_last_lines: int = Field(default=50, ge=0, le=1000)
    environment: dict[str, str] = Field(default_factory=dict, repr=False)

    @field_validator("environment")
    @classmethod
    def valid_environment(cls, value: dict[str, str]) -> dict[str, str]:
        if any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in item
            for key, item in value.items()
        ):
            raise ValueError("invalid shell environment")
        return value

    @field_validator("working_dir")
    @classmethod
    def absolute_working_dir(cls, value: str) -> str:
        if not value.startswith("/") or ".." in PurePosixPath(value).parts or "\x00" in value:
            raise ValueError("working_dir must be an absolute container path")
        return value


@dataclass(frozen=True)
class CommandResult:
    output: str
    exit_code: int | None
    timed_out: bool
    output_limited: bool
    summary: str
    audit_path: Path

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not (self.timed_out or self.output_limited)
