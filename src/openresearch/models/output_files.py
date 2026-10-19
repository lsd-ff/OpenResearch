import base64
import hashlib
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from openresearch.errors import ContractError
from openresearch.models.runtime import ContractModel
from openresearch.sandbox.file_transfer import relative_path

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".json": "application/json",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".py": "text/x-python",
}


class OutputFile(ContractModel):
    path: str = Field(max_length=256)
    size: int = Field(ge=0, le=1048576)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    base64: str = Field(max_length=1400000)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        try:
            return relative_path(value)
        except ContractError:
            raise ValueError("output file path must stay inside the artifact workspace") from None

    @property
    def media_type(self) -> str:
        return MEDIA_TYPES.get(PurePosixPath(self.path).suffix.lower(), "application/octet-stream")

    def content(self) -> bytes:
        return base64.b64decode(self.base64, validate=True)

    @model_validator(mode="after")
    def verify_content(self) -> "OutputFile":
        raw = self.content()
        if len(raw) != self.size or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("output file size or checksum mismatch")
        return self


class OutputFiles(ContractModel):
    version: Literal["execution.files/v1"] = "execution.files/v1"
    files: list[OutputFile] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique_paths(self) -> "OutputFiles":
        if len({item.path for item in self.files}) != len(self.files):
            raise ValueError("output file paths must be unique")
        if sum(item.size for item in self.files) > 1048576:
            raise ValueError("output files exceed total size limit")
        return self

    def preview(self) -> str:
        for item in self.files:
            if item.media_type.startswith("image/") and item.size:
                return f"data:{item.media_type};base64,{item.base64}"
        return ""
