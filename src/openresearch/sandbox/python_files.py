import base64
import json
from collections.abc import Awaitable, Callable

from openresearch.errors import SandboxError
from openresearch.models.runtime import ExecutionResult
from openresearch.sandbox.base import LogCallback
from openresearch.sandbox.file_transfer import WORKSPACE, relative_path

Executor = Callable[[str, str, LogCallback], Awaitable[ExecutionResult]]


async def ignore_log(stream: str, message: str) -> None:
    pass


def checked_path(path: str) -> str:
    target = json.dumps(f"{WORKSPACE}/{relative_path(path)}")
    return (
        "from pathlib import Path\n"
        f"root = Path({json.dumps(WORKSPACE)})\np = Path({target})\n"
        "root.mkdir(parents=True, exist_ok=True)\n"
        "assert not root.is_symlink() and p.resolve().is_relative_to(root)\n"
        "assert not p.is_symlink()\n"
    )


async def put_files(execute: Executor, sandbox_id: str, files: dict[str, bytes]) -> None:
    for path, content in files.items():
        prefix = checked_path(path) + "import base64\np.parent.mkdir(parents=True, exist_ok=True)\n"
        chunks = [content[index : index + 32768] for index in range(0, len(content), 32768)] or [
            b""
        ]
        for index, chunk in enumerate(chunks):
            data = json.dumps(base64.b64encode(chunk).decode("ascii"))
            mode = "wb" if index == 0 else "ab"
            script = (
                prefix
                + f"with p.open({mode!r}) as output:\n    output.write(base64.b64decode({data}))\n"
            )
            result = await execute(sandbox_id, script, ignore_log)
            if result.exit_code != 0:
                raise SandboxError("sandbox rejected an input file")


async def get_file(execute: Executor, sandbox_id: str, path: str, max_bytes: int) -> bytes:
    script = checked_path(path) + (
        f"import base64\nassert p.stat().st_size <= {max_bytes}\n"
        f"with p.open('rb') as source:\n    raw = source.read({max_bytes + 1})\n"
        f"assert len(raw) <= {max_bytes}\nprint(base64.b64encode(raw).decode('ascii'))\n"
    )
    result = await execute(sandbox_id, script, ignore_log)
    if result.exit_code != 0:
        raise SandboxError("sandbox output file is missing or violates its contract")
    try:
        content = base64.b64decode(result.stdout.strip(), validate=True)
    except ValueError as exc:
        raise SandboxError("sandbox returned invalid file data") from exc
    if len(content) > max_bytes:
        raise SandboxError("sandbox output file exceeds configured limit")
    return content
