import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

from openresearch.errors import SandboxError
from openresearch.models.runtime import new_id


def runtime_namespace(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "runtime-id"
    if not path.exists():
        descriptor, name = tempfile.mkstemp(dir=root, prefix=".runtime-")
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(new_id())
                stream.flush()
                os.fsync(stream.fileno())
            with suppress(FileExistsError):
                # 持久化实例 ID 与数据卷绑定；不同容器中的同名 /data 不会发生冲突。
                os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    identifier = path.read_text(encoding="ascii")
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise SandboxError("invalid persistent runtime namespace")
    return identifier
