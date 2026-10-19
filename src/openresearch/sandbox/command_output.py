import base64

from openresearch.errors import SandboxError
from openresearch.models.runtime import ExecutionResult
from openresearch.sandbox.base import LogCallback

PREFIX = "OPENRESEARCH_STREAM_V1:"


class CommandOutput:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.buffer = ""
        self.output: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
        self.total = 0

    async def feed(self, text: str, bare_json: bool, on_log: LogCallback) -> None:
        # 编码帧恢复原始字节，旧 execd 丢弃行尾也不会改变程序的 stdout/stderr。
        self.buffer += text + ("\n" if bare_json and not text.endswith("\n") else "")
        while "\n" in self.buffer:
            frame, self.buffer = self.buffer.split("\n", 1)
            if not frame:
                continue
            if len(frame) > 65536:
                raise SandboxError("OpenSandbox output frame exceeds limit")
            if not frame.startswith(PREFIX):
                raise SandboxError("invalid OpenSandbox execution output frame")
            channel, separator, encoded = frame[len(PREFIX) :].partition(":")
            if channel not in self.output or not separator:
                raise SandboxError("invalid OpenSandbox output channel")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise SandboxError("invalid OpenSandbox output encoding") from None
            self.total += len(raw)
            if self.total > self.limit:
                raise SandboxError("sandbox output exceeds configured size limit")
            self.output[channel].append(raw)
            await on_log(channel, raw.decode("utf-8", errors="replace"))
        if len(self.buffer) > 65536:
            raise SandboxError("OpenSandbox output frame exceeds limit")

    def result(self, exit_code: int) -> ExecutionResult:
        if self.buffer.strip():
            raise SandboxError("OpenSandbox output ended inside an execution frame")
        return ExecutionResult(
            stdout=b"".join(self.output["stdout"]).decode("utf-8", errors="replace"),
            stderr=b"".join(self.output["stderr"]).decode("utf-8", errors="replace"),
            exit_code=exit_code,
        )
