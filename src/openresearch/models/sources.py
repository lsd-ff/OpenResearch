import re
from typing import Literal
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from pydantic import Field, field_validator, model_validator

from openresearch.models.runtime import ContractModel
from openresearch.models.runtime_profiles import TargetPython
from openresearch.sandbox.file_transfer import relative_path


class RepositorySpec(ContractModel):
    url: str
    ref: str = Field(default="HEAD", min_length=1, max_length=128)

    @field_validator("url")
    @classmethod
    def github_repository(cls, value: str) -> str:
        parts = urlsplit(value)
        path = parts.path.removesuffix("/").removesuffix(".git")
        if (
            parts.scheme != "https"
            or parts.netloc.lower() != "github.com"
            or parts.query
            or parts.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path)
        ):
            raise ValueError("repository must be an HTTPS GitHub repository URL")
        return "https://github.com" + path

    @field_validator("ref")
    @classmethod
    def safe_ref(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value) or len(value.encode()) > 256:
            raise ValueError("invalid repository reference")
        return value

    @property
    def slug(self) -> str:
        return self.url.removeprefix("https://github.com/")


class SourceFile(ContractModel):
    path: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return relative_path(value)


class RepositorySnapshot(ContractModel):
    version: Literal["repository.snapshot/v1"] = "repository.snapshot/v1"
    url: str
    requested_ref: str
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    files: list[SourceFile]


class EnvironmentSpec(ContractModel):
    requirements: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("requirements")
    @classmethod
    def exact_pins(cls, values: list[str]) -> list[str]:
        output = []
        names: set[str] = set()
        for value in values:
            try:
                requirement = Requirement(value)
            except InvalidRequirement as exc:
                raise ValueError("invalid Python dependency") from exc
            pins = list(requirement.specifier)
            if (
                requirement.url
                or requirement.marker
                or requirement.extras
                or len(pins) != 1
                or pins[0].operator != "=="
                or "*" in pins[0].version
            ):
                raise ValueError("dependencies require exact name==version pins without URLs")
            name = canonicalize_name(requirement.name)
            if name in names:
                raise ValueError("duplicate dependency pin")
            names.add(name)
            output.append(f"{name}=={pins[0].version}")
        return output


class LockedWheel(ContractModel):
    requirement: str
    filename: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=1)
    url: str

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if "/" in value or relative_path(value) != value or not value.endswith(".whl"):
            raise ValueError("invalid wheel filename")
        return value


class EnvironmentLock(ContractModel):
    version: Literal["environment.lock/v1"] = "environment.lock/v1"
    python: TargetPython = "3.12"
    platform: Literal["linux_x86_64"] = "linux_x86_64"
    wheels: list[LockedWheel] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def unique_wheels(self) -> "EnvironmentLock":
        if len({wheel.requirement for wheel in self.wheels}) != len(self.wheels):
            raise ValueError("duplicate wheel requirements")
        return self


class PackageDescription(ContractModel):
    name: str
    version: str
    summary: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=12000)
    requires_python: str = Field(default="", max_length=256)
    project_urls: dict[str, str] = Field(default_factory=dict, max_length=10)
    metadata_url: str
    metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
