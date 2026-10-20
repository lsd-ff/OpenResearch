import json
from functools import cache
from importlib import resources

from openresearch.models.runtime_profiles import TargetPython


@cache
def stdlib_names(python: TargetPython) -> frozenset[str]:
    # 来自实际目标 CPython/Linux 容器，文件同时保留镜像摘要；不使用宿主机版本的模块清单。
    records = json.loads(
        resources.files("openresearch.sources").joinpath("stdlib.json").read_text(encoding="utf-8")
    )
    return frozenset(records[python]["modules"])


def is_stdlib(name: str) -> bool:
    normalized = name.casefold().replace("-", "_")
    # 被新版本移除的标准库也不能悄悄换成同名 PyPI 包；应调整运行时或明确变更程序。
    return any(normalized in stdlib_names(version) for version in ("3.11", "3.12", "3.13"))
