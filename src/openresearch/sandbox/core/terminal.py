import re
import shlex
import socket
import threading
import time
from contextlib import suppress
from queue import Empty, Full, Queue
from typing import Any
from uuid import uuid4

from openresearch.errors import SandboxError
from openresearch.sandbox.core.models import CommandResult
from openresearch.sandbox.core.output import OutputCapture


class Terminal:
    """一个真实 Bash TTY；由上层串行化命令，读线程同时支持 Unix socket 和 Windows pipe。"""

    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.socket = getattr(stream, "_sock", stream)
        self.queue: Queue[bytes | Exception] = Queue(maxsize=16)
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self._read, daemon=True, name="openresearch-core-tty")
        self.reader.start()

    def _read(self) -> None:
        while not self.closed.is_set():
            try:
                chunk = self.socket.recv(65536)
                item: bytes | Exception = chunk if chunk else SandboxError("core terminal EOF")
            except Exception as exc:
                item = exc
            while not self.closed.is_set():
                try:
                    self.queue.put(item, timeout=0.1)
                    break
                except Full:
                    continue
            if isinstance(item, Exception):
                return

    def _send(self, payload: bytes) -> None:
        try:
            self.socket.sendall(payload)
        except Exception as exc:
            raise SandboxError("cannot write to core terminal") from exc

    @staticmethod
    def _marker(token: str) -> bytes:
        return b"\x1e" + token.encode() + b":"

    def _interrupt(self, token: str) -> None:
        # SIGINT 后发独立屏障。原 eval 行可能被 Bash 丢弃，不能继续等待原结束标记。
        self._send(b"\x03\n" + self._printf(token) + b"\n")

    @staticmethod
    def _printf(token: str) -> bytes:
        return f"builtin printf '\\036{token}:%s\\037' \"$?\"".encode()

    def execute(
        self, command: str, capture: OutputCapture, timeout: float, cancel: threading.Event
    ) -> CommandResult:
        token = uuid4().hex
        start = b"\x1e" + token.encode() + b"\x1f"
        payload = (
            (
                f"builtin printf '\\036{token}\\037'; "
                f"builtin eval -- {shlex.quote(command)} </dev/null; "
            ).encode()
            + self._printf(token)
            + b"\n"
        )
        self._send(payload)
        pending = b""
        started, timed_out = False, False
        recovery: str | None = None
        deadline = time.monotonic() + timeout
        while True:
            expired = time.monotonic() >= deadline
            if recovery is None and (expired or cancel.is_set() or capture.limited):
                timed_out = expired
                recovery = uuid4().hex
                self._interrupt(recovery)
                deadline = time.monotonic() + 3
            elif recovery is not None and expired:
                raise SandboxError("core terminal did not recover after interruption")
            try:
                chunk = self.queue.get(timeout=0.1)
            except Empty:
                continue
            if isinstance(chunk, Exception):
                capture.write(pending)
                raise SandboxError("core terminal disconnected") from chunk
            pending += chunk
            if not started and recovery is None:
                position = pending.find(start)
                if position < 0:
                    pending = pending[-len(start) :]
                    continue
                pending = pending[position + len(start) :]
                started = True
            marker = self._marker(recovery or token)
            match = re.search(re.escape(marker) + rb"([0-9]{1,3})\x1f", pending)
            if match:
                if started:
                    capture.write(pending[: match.start()])
                code = None if recovery else int(match[1])
                return capture.finish(code, timed_out, cancel.is_set())
            keep = len(marker) + 8
            if len(pending) > keep:
                if started:
                    capture.write(pending[:-keep])
                pending = pending[-keep:]

    def close(self) -> None:
        self.closed.set()
        with suppress(OSError, AttributeError):
            self.socket.shutdown(socket.SHUT_RDWR)
        self.stream.close()
        if self.socket is not self.stream:
            self.socket.close()
        self.reader.join(timeout=2)
        if self.reader.is_alive():
            raise SandboxError("core terminal reader did not stop")
