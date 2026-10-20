"""可信资源元数据探测，仅在目标沙箱内执行，不安装任何实验依赖。"""

import csv
import ctypes
import io
import json
import os
import platform
import subprocess
from pathlib import Path


def limit(path: str) -> int | None:
    value = Path(path).read_text().strip()
    return None if value == "max" else int(value)


cpu = float(os.cpu_count() or 0)
# Linux 专用程序；经模块字典取接口，宿主 Windows 的类型检查不假定其存在。
sysconf = vars(os)["sysconf"]
memory = int(sysconf("SC_PHYS_PAGES")) * int(sysconf("SC_PAGE_SIZE"))
try:
    quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
    if quota != "max":
        cpu = min(cpu, int(quota) / int(period))
    maximum = limit("/sys/fs/cgroup/memory.max")
    if maximum is not None:
        memory = min(memory, maximum)
except FileNotFoundError:
    if Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").exists():
        legacy_quota = limit("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
        legacy_period = limit("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if legacy_quota and legacy_quota > 0 and legacy_period:
            cpu = min(cpu, legacy_quota / legacy_period)
        maximum = limit("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        if maximum is not None:
            memory = min(memory, maximum)
disk = vars(os)["statvfs"]("/workspace")
devices, gpu_error = [], ""
if os.environ.get("OPENRESEARCH_PROBE_GPU") == "1":
    try:
        driver = ctypes.CDLL("libcuda.so.1")
        count = ctypes.c_int()
        assert driver.cuInit(0) == 0 and driver.cuDeviceGetCount(ctypes.byref(count)) == 0
        assert count.value > 0, "CUDA reported no visible devices"
        output = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        devices = [
            {"name": name.strip(), "memory_mb": int(total), "free_memory_mb": int(free)}
            for name, total, free in csv.reader(io.StringIO(output.stdout))
        ]
        assert len(devices) == count.value, "CUDA and driver inventory disagree"
    except Exception as exc:
        devices, gpu_error = [], ("GPU probe unavailable: " + str(exc))[-2000:]
print(
    json.dumps(
        {
            "python": platform.python_version(),
            "platform": platform.system(),
            "cpu_count": cpu,
            "memory_mb": memory // 1048576,
            "workspace_free_mb": disk.f_bavail * disk.f_frsize // 1048576,
            "gpus": devices,
            "gpu_error": gpu_error,
        }
    )
)
