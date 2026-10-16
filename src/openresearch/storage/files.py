import hashlib
import os
import re
from pathlib import Path

from openresearch.errors import ContractError
from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import Artifact, new_id


class ArtifactStore:
    def __init__(self, root: Path, max_bytes: int = 1024 * 1024) -> None:
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, lease: ExecutionLease, key: str, content: str) -> Artifact:
        """按执行租约写入不可变文件，返回可入库的摘要与预览。"""
        for identifier in (lease.plan_id, lease.task_id, lease.execution_id):
            if not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise ContractError("invalid artifact resource identifier")
        raw = content.encode("utf-8")
        if len(raw) > self.max_bytes:
            raise ContractError("artifact exceeds configured size limit")
        identifier = new_id()
        relative = Path(lease.plan_id) / lease.execution_id / f"{identifier}.txt"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        # 每次执行使用新文件名，重试不会覆盖前一次执行的证据。
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return Artifact(
            id=identifier,
            key=key,
            type="code" if key == "generated_code" else "json",
            producer_task_id=lease.task_id,
            execution_id=lease.execution_id,
            value=content[:4096],
            location=relative.as_posix(),
            size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )

    def read(self, artifact: Artifact) -> str:
        path = (self.root / artifact.location).resolve()
        if not path.is_relative_to(self.root):
            raise ContractError("artifact path escapes its storage root")
        if path.stat().st_size != artifact.size or artifact.size > self.max_bytes:
            raise ContractError("artifact size mismatch")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise ContractError("artifact hash mismatch")
        return raw.decode("utf-8")
