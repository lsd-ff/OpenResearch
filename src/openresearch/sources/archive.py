import hashlib
import io
import tarfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from openresearch.errors import ContractError
from openresearch.models.sources import SourceFile
from openresearch.sandbox.file_transfer import relative_path


@dataclass(frozen=True)
class ArchiveLimits:
    total_bytes: int
    file_bytes: int
    file_count: int


def unpack_repository(raw: bytes, limits: ArchiveLimits) -> dict[str, bytes]:
    """只读取常规文件到内存；归档路径、链接和总解压量全部由服务端验证。"""
    output: dict[str, bytes] = {}
    total = 0
    root: str | None = None
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for index, member in enumerate(archive):
                if index >= limits.file_count * 2 + 1:
                    raise ContractError("archive exceeds entry count budget")
                path = PurePosixPath(relative_path(member.name.rstrip("/")))
                root = root or path.parts[0]
                if path.parts[0] != root:
                    raise ContractError("archive has multiple roots")
                if member.isdir():
                    continue
                if not member.isfile() or len(path.parts) < 2:
                    raise ContractError("archive links and special files are forbidden")
                key = relative_path(PurePosixPath(*path.parts[1:]).as_posix())
                total += member.size
                if key in output or member.size > limits.file_bytes:
                    raise ContractError("archive contains duplicate or oversized files")
                if total > limits.total_bytes or len(output) >= limits.file_count:
                    raise ContractError("archive exceeds extraction budget")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ContractError("archive file is unreadable")
                with stream:
                    output[key] = stream.read(limits.file_bytes + 1)
                if len(output[key]) != member.size:
                    raise ContractError("archive file size mismatch")
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise ContractError("invalid repository archive") from exc
    if not output:
        raise ContractError("repository snapshot is empty")
    return output


def inventory(files: dict[str, bytes]) -> list[SourceFile]:
    return [
        SourceFile(path=path, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        for path, raw in sorted(files.items())
    ]
