import os
from pathlib import Path


def filesystem_path(path: Path) -> Path:
    """本地文件操作使用绝对路径；Windows 显式支持深层审计目录。"""
    resolved = path.resolve()
    value = str(resolved)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return resolved
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)
