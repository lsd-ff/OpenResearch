from contextlib import closing
from typing import Any

import docker
from docker.utils.socket import frames_iter

from openresearch.errors import SandboxError
from openresearch.sandbox.file_transfer import archive_files
from openresearch.sandbox.python_files import checked_path


def managed_container(client: Any, sandbox_id: str) -> None:
    if client.containers.get(sandbox_id).labels.get("openresearch.managed") != "true":
        raise SandboxError("sandbox is not managed by this runtime")


def send_archive(connection: Any, archive: bytes) -> None:
    sender = connection._sock if hasattr(connection, "_sock") else connection
    sender.settimeout(30)
    position = 0
    while position < len(archive):
        written = sender.send(archive[position : position + 65536])
        if not isinstance(written, int) or written <= 0:
            raise SandboxError("sandbox input stream closed during transfer")
        position += written


def put_files(sandbox_id: str, files: dict[str, bytes]) -> None:
    archive = archive_files(files)
    script = (
        "import sys, io, tarfile\n"
        f"raw = sys.stdin.buffer.read({len(archive)})\n"
        f"assert len(raw) == {len(archive)}\n"
        "with tarfile.open(fileobj=io.BytesIO(raw)) as archive:\n"
        "    archive.extractall('/tmp', filter='data')\n"
    )
    # docker cp 拒绝只读 rootfs 上的 tmpfs，使用容器自己的进程向可写 tmpfs 落盘。
    with closing(docker.from_env(timeout=30)) as client:
        managed_container(client, sandbox_id)
        execution = client.api.exec_create(sandbox_id, ["python", "-c", script], stdin=True)
        with closing(client.api.exec_start(execution["Id"], socket=True)) as connection:
            send_archive(connection, archive)
            output = bytearray()
            for _, chunk in frames_iter(connection, tty=False):
                output.extend(chunk)
                if len(output) > 8192:
                    raise SandboxError("sandbox file transfer returned excessive output")
            code = client.api.exec_inspect(execution["Id"]).get("ExitCode")
            if code != 0:
                raise SandboxError(
                    f"sandbox rejected input files: {output.decode(errors='replace')[:1000]}"
                )


def get_file(sandbox_id: str, path: str, max_bytes: int) -> bytes:
    script = checked_path(path) + (
        f"import sys\nassert p.stat().st_size <= {max_bytes}\n"
        f"with p.open('rb') as source:\n    raw = source.read({max_bytes + 1})\n"
        f"assert len(raw) <= {max_bytes}\nsys.stdout.buffer.write(raw)\n"
    )
    with closing(docker.from_env(timeout=30)) as client:
        managed_container(client, sandbox_id)
        execution = client.api.exec_create(sandbox_id, ["python", "-c", script])
        stream = client.api.exec_start(execution["Id"], stream=True, demux=True)
        with closing(stream):
            output = collect_file(stream, max_bytes)
            if client.api.exec_inspect(execution["Id"]).get("ExitCode") != 0:
                raise SandboxError("sandbox output file is missing or violates its contract")
        return output


def collect_file(stream: Any, max_bytes: int) -> bytes:
    output = bytearray()
    errors = 0
    for stdout, stderr in stream:
        if stdout:
            output.extend(stdout)
        errors += len(stderr or b"")
        if len(output) > max_bytes or errors > 8192:
            raise SandboxError("sandbox output file exceeds configured limit")
    return bytes(output)
