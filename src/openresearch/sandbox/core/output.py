import hashlib
import json
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from openresearch.sandbox.core.models import CommandResult, CoreConfig
from openresearch.storage.localpath import filesystem_path


class OutputCapture:
    """原始合并 TTY 输出与有界推理摘要分别保留，超过字节预算不能判为成功。"""

    def __init__(self, root: Path, command: str, config: CoreConfig) -> None:
        self.config, self.command = config, command
        folder = filesystem_path(root) / "commands" / uuid4().hex
        folder.mkdir(parents=True)
        self.path = folder / "output.bin"
        self.stream = self.path.open("wb")
        self.size = 0
        self.limited = False
        self.metadata: dict[str, object] | None = None

    def write(self, value: bytes) -> None:
        remaining = self.config.max_output_bytes - self.size
        self.limited |= len(value) > remaining
        self.stream.write(value[:remaining])
        self.size += min(len(value), remaining)

    def finish(
        self, exit_code: int | None, timed_out: bool, canceled: bool = False
    ) -> CommandResult:
        self.stream.flush()
        raw = self.path.read_bytes()
        output = raw.decode("utf-8", errors="replace")
        lines = output.splitlines(keepends=True)
        first, last = self.config.keep_first_lines, self.config.keep_last_lines
        summary = output
        if len(lines) > first + last:
            tail = lines[-last:] if last else []
            summary = (
                "".join(lines[:first])
                + f"\n... [{len(lines) - first - last} lines omitted] ...\n"
                + "".join(tail)
            )
        metadata: dict[str, object] = {
            "command": self.command,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "canceled": canceled,
            "output_limited": self.limited,
            "captured_bytes": len(raw),
            "captured_sha256": hashlib.sha256(raw).hexdigest(),
        }
        self.path.with_suffix(".json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        self.metadata = metadata
        return CommandResult(output, exit_code, timed_out, self.limited, summary, self.path)

    def __enter__(self) -> "OutputCapture":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc is not None:
                self.stream.flush()
                metadata = self.metadata or {"command": self.command}
                metadata.update({"error_type": type(exc).__name__, "partial": True})
                self.path.with_suffix(".json").write_text(
                    json.dumps(metadata, ensure_ascii=False),
                    encoding="utf-8",
                )
        finally:
            self.stream.close()
