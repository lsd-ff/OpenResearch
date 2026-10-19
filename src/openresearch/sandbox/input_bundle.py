"""HTTP 批量传输仅接受受限 tar 普通文件，解析不落盘。"""

import io
import tarfile
from pathlib import PurePosixPath

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.sandbox.file_transfer import relative_path


def bundle_limit(settings: Settings) -> int:
    return (
        settings.source_max_bytes
        + settings.dependency_max_bytes
        + (settings.source_max_files + 64) * 2048
    )


def unpack_bundle(raw: bytes, settings: Settings) -> dict[str, bytes]:
    output: dict[str, bytes] = {}
    total = 0
    maximum = settings.source_max_bytes + settings.dependency_max_bytes
    if len(raw) > bundle_limit(settings):
        raise ContractError("input bundle exceeds transfer limit")
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for index, member in enumerate(archive):
                if index >= (settings.source_max_files + 64) * 2:
                    raise ContractError("input bundle exceeds entry limit")
                path = PurePosixPath(relative_path(member.name.rstrip("/")))
                if path.parts[0] != "openresearch":
                    raise ContractError("input bundle must stay inside its workspace")
                if member.isdir():
                    continue
                if not member.isfile() or len(path.parts) < 2:
                    raise ContractError("input bundle links and special files are forbidden")
                key = relative_path(PurePosixPath(*path.parts[1:]).as_posix())
                total += member.size
                if (
                    key in output
                    or total > maximum
                    or len(output) >= settings.source_max_files + 32
                ):
                    raise ContractError("input bundle contains duplicate or oversized files")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ContractError("input bundle file is unreadable")
                with stream:
                    output[key] = stream.read(member.size + 1)
                if len(output[key]) != member.size:
                    raise ContractError("input bundle size mismatch")
    except (tarfile.TarError, ValueError, OSError) as exc:
        raise ContractError("invalid input file bundle") from exc
    return output
