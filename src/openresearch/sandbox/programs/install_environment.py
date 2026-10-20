"""可信安装程序，只在沙箱中运行，严格禁止构建源码分发包和联网安装。"""

import importlib.metadata
import json
import os
import pathlib
import platform
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openresearch.sources.dependency_closure import validate_dependency_graph

from pip._vendor.packaging.markers import default_environment
from pip._vendor.packaging.utils import canonicalize_name

root = pathlib.Path("/tmp/openresearch")
target = pathlib.Path("/workspace/environment")
target.mkdir(parents=True, exist_ok=True)
scratch = pathlib.Path("/workspace/install-tmp")
scratch.mkdir(parents=True, exist_ok=True)
expected_python = globals().get("OPENRESEARCH_EXPECTED_PYTHON", (3, 12))
assert sys.version_info[:2] == expected_python, (
    f"environment lock requires CPython {expected_python}"
)
expected_full_version = globals().get("OPENRESEARCH_EXPECTED_FULL_VERSION")
assert expected_full_version is None or platform.python_version() == expected_full_version, (
    "actual interpreter changed after dependency resolution: "
    f"expected {expected_full_version}, got {platform.python_version()}"
)
assert platform.system() == "Linux" and platform.machine() == "x86_64", "unsupported target"
assert tuple(int(part) for part in platform.libc_ver()[1].split(".")[:2]) >= (2, 36), (
    "runtime requires glibc >= 2.36"
)
if (root / "requirements.lock").read_text().strip():
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--isolated",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--no-index",
            "--no-deps",
            "--only-binary=:all:",
            "--require-hashes",
            "--no-compile",
            "--find-links",
            str(root / "wheels"),
            "--target",
            str(target),
            "-r",
            str(root / "requirements.lock"),
        ],
        check=True,
        # wheel 与其解压内容分别占用 tmpfs；在目标所在内存盘解压，避免填满输入盘。
        env={**os.environ, "TMPDIR": str(scratch)},
    )
distributions = list(importlib.metadata.distributions(path=[str(target)]))
installed = {canonicalize_name(item.metadata["Name"]): item.version for item in distributions}
# 以容器实际 Python 补丁版本再次验证 wheel 内元数据，避免服务端目标版本与运行版本的 marker 差异。
try:
    validate_dependency_graph(
        {canonicalize_name(item.metadata["Name"]): item.requires or [] for item in distributions},
        installed,
        default_environment(),
    )
except ValueError as error:
    raise RuntimeError(f"installed environment has an unsatisfied dependency: {error}") from error
report = {
    "python": platform.python_version(),
    "platform": platform.platform(),
    "installed": installed,
}
(root / "environment.json").write_text(json.dumps(report))
