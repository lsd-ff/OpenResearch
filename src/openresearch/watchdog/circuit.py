import hashlib
import threading
from collections import deque


class CircuitBreaker:
    def __init__(self, window_size: int = 5) -> None:
        if not 1 <= window_size <= 100:
            raise ValueError("invalid circuit failure window")
        self._window: deque[bytes] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def record_and_check(self, command: str, output: str) -> bool:
        # 两个独立摘要避免 cmd+output 拼接边界含糊；窗口只接收已确认的失败。
        digest = (
            hashlib.sha256(command.encode()).digest() + hashlib.sha256(output.encode()).digest()
        )
        with self._lock:
            self._window.append(digest)
            return len(self._window) == self._window.maxlen and all(
                item == digest for item in self._window
            )

    def reset(self) -> None:
        with self._lock:
            self._window.clear()
