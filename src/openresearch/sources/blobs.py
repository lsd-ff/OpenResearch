import hashlib
import os
import re
import tempfile
from pathlib import Path

from openresearch.errors import ContractError


class SourceBlobs:
    """来源文件按内容摘要保存；路径不来自仓库，读回时再次验证摘要。"""

    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ContractError("invalid source content digest")
        return self.root / digest[:2] / digest

    def write(self, raw: bytes) -> str:
        if len(raw) > self.max_bytes:
            raise ContractError("source content exceeds configured limit")
        digest = hashlib.sha256(raw).hexdigest()
        path = self._path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # 原子发布完整内容，避免另一个 Worker 看见只写入了一半的共享摘要文件。
            try:
                os.link(temporary, path)
            except FileExistsError:
                self.read(digest, len(raw))
        finally:
            temporary.unlink(missing_ok=True)
        return digest

    def read(self, digest: str, size: int) -> bytes:
        path = self._path(digest)
        if size > self.max_bytes or path.stat().st_size != size:
            raise ContractError("source content size mismatch")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ContractError("source content checksum mismatch")
        return raw
