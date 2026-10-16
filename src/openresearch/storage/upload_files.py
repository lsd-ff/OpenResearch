import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from openresearch.errors import ContractError, UnsupportedMediaError, UploadTooLargeError
from openresearch.models.context import Identity
from openresearch.models.documents import UploadRecord
from openresearch.models.runtime import new_id

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".ipynb",
    ".csv",
    ".tsv",
    ".jsonl",
}


@dataclass
class UploadSubmission:
    name: str
    stream: BinaryIO
    identity: Identity


def safe_name(name: str) -> str:
    filename = PurePosixPath(name.replace("\\", "/")).name
    filename = "".join(character for character in filename if ord(character) >= 32)
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise UnsupportedMediaError("unsupported file type")
    return filename[:160] + suffix if len(filename) > 180 else filename


def content_type(name: str, prefix: bytes) -> str:
    if name.lower().endswith(".pdf"):
        if not prefix.startswith(b"%PDF-"):
            raise UnsupportedMediaError("file content does not match PDF extension")
        return "application/pdf"
    if b"\x00" in prefix:
        raise UnsupportedMediaError("binary content is not a supported text upload")
    try:
        prefix.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # 前缀可能截断多字节字符，最终完整 UTF-8 校验在读取文本时完成。
        if exc.reason != "unexpected end of data":
            raise UnsupportedMediaError("text uploads must use UTF-8") from exc
    return (
        "application/json" if name.lower().endswith((".json", ".jsonl", ".ipynb")) else "text/plain"
    )


class UploadFiles:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def write(self, submission: UploadSubmission) -> UploadRecord:
        name = safe_name(submission.name)
        prefix = submission.stream.read(512)
        if not prefix:
            raise ContractError("uploaded file is empty")
        media_type = content_type(name, prefix)
        identifier = new_id()
        location = f"{identifier}/content{PurePosixPath(name).suffix.lower()}"
        path = self.root / location
        path.parent.mkdir(parents=True, exist_ok=False)
        try:
            size, digest = self._persist(path, submission.stream, prefix)
        except BaseException:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
            raise
        return UploadRecord(
            id=identifier,
            name=name,
            content_type=media_type,
            location=location,
            owner_id=submission.identity.user_id,
            session_id=submission.identity.session_id,
            size=size,
            sha256=digest,
        )

    def _persist(self, path: Path, source: BinaryIO, prefix: bytes) -> tuple[int, str]:
        size = 0
        digest = hashlib.sha256()
        with path.open("xb") as destination:
            chunk = prefix
            while chunk:
                size += len(chunk)
                if size > self.max_bytes:
                    raise UploadTooLargeError("uploaded file exceeds configured size limit")
                destination.write(chunk)
                digest.update(chunk)
                chunk = source.read(65536)
            destination.flush()
            os.fsync(destination.fileno())
        return size, digest.hexdigest()

    def read(self, record: UploadRecord) -> bytes:
        path = (self.root / record.location).resolve()
        if not path.is_relative_to(self.root):
            raise ContractError("upload path escapes its storage root")
        if not path.is_file() or path.stat().st_size != record.size or record.size > self.max_bytes:
            raise ContractError("upload is missing or its size changed")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record.sha256:
            raise ContractError("upload hash mismatch")
        return raw

    def discard(self, record: UploadRecord) -> None:
        path = (self.root / record.location).resolve()
        if path.is_relative_to(self.root):
            path.unlink(missing_ok=True)
