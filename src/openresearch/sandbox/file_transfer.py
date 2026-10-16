import io
import tarfile
from pathlib import PurePosixPath

from openresearch.errors import ContractError, SandboxError

WORKSPACE = "/tmp/openresearch"


def relative_path(path: str) -> str:
    value = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or ":" in path
        or any(ord(character) < 32 for character in path)
        or value.is_absolute()
        or ".." in value.parts
    ):
        raise ContractError("sandbox file path must stay inside its workspace")
    if value.as_posix() == ".":
        raise ContractError("sandbox file path must identify a file")
    return value.as_posix()


def archive_files(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        directories: set[str] = set()
        for path, content in files.items():
            name = PurePosixPath("openresearch") / relative_path(path)
            for directory in reversed(name.parents):
                if directory.as_posix() == "." or directory.as_posix() in directories:
                    continue
                directories.add(directory.as_posix())
                entry = tarfile.TarInfo(directory.as_posix())
                entry.type, entry.mode, entry.uid, entry.gid = tarfile.DIRTYPE, 0o700, 65534, 65534
                archive.addfile(entry)
            entry = tarfile.TarInfo(name.as_posix())
            entry.size, entry.mode, entry.uid, entry.gid = len(content), 0o600, 65534, 65534
            archive.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


def unpack_file(raw: bytes, path: str, max_bytes: int) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        members = archive.getmembers()
        if len(members) != 1:
            raise SandboxError("sandbox returned an unexpected file archive")
        member = members[0]
        if (
            not member.isfile()
            or member.name != PurePosixPath(path).name
            or member.size > max_bytes
        ):
            raise SandboxError("sandbox output file violates its contract")
        stream = archive.extractfile(member)
        if stream is None:
            raise SandboxError("sandbox output file is missing")
        content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise SandboxError("sandbox output file exceeds configured limit")
        return content
