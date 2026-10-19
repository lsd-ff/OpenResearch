"""只在沙箱中运行：把子进程的原始输出转为有限大小的逐行传输帧。"""

import base64
import os
import subprocess
import sys
import threading
from typing import BinaryIO

PREFIX = "OPENRESEARCH_STREAM_V1:"


def forward_stream(source: BinaryIO, channel: str, guard: threading.Lock) -> None:
    while True:
        raw = os.read(source.fileno(), 8192)
        if not raw:
            return
        frame = PREFIX + channel + ":" + base64.b64encode(raw).decode("ascii")
        # 两个读取线程共用输出管道；锁覆盖整个帧，防止 stdout/stderr 交叉破坏编码。
        with guard:
            print(frame, flush=True)


def run_python(code: str) -> int:
    child = subprocess.Popen(
        [sys.executable, "-I", "-u", "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert child.stdout is not None and child.stderr is not None
    guard = threading.Lock()
    readers = [
        threading.Thread(target=forward_stream, args=(pipe, channel, guard), daemon=True)
        for pipe, channel in ((child.stdout, "stdout"), (child.stderr, "stderr"))
    ]
    for reader in readers:
        reader.start()
    exit_code = child.wait()
    for reader in readers:
        reader.join()
    child.stdout.close()
    child.stderr.close()
    return exit_code
