"""仅在实验容器中执行的有限文件收集程序。"""

import base64
import hashlib
import json
import os
import stat


def collect_output_files(max_bytes: int) -> dict[str, object]:
    root = "/workspace/artifacts"
    if not os.path.lexists(root):
        return {"version": "execution.files/v1", "files": []}
    # 基于目录描述符遍历，拒绝链接、设备及 FIFO，防止输出文件换名时越过工作区。
    linux_api = vars(os)
    directory_flag = int(linux_api["O_DIRECTORY"])
    nofollow_flag = int(linux_api["O_NOFOLLOW"])
    nonblock_flag = int(linux_api["O_NONBLOCK"])
    walker = linux_api["fwalk"]  # 容器使用 Linux API，宿主机仍可做跨平台类型检查。
    root_fd = os.open(root, os.O_RDONLY | directory_flag | nofollow_flag)
    files: list[dict[str, str | int]] = []
    total = 0
    visited = 0
    try:
        for directory, folders, names, parent in walker(".", dir_fd=root_fd, follow_symlinks=False):
            visited += 1
            if visited > 128:
                raise ValueError("too many output directories")
            for folder in folders:
                if stat.S_ISLNK(os.stat(folder, dir_fd=parent, follow_symlinks=False).st_mode):
                    raise ValueError("linked output directory is forbidden")
            for name in names:
                if len(files) >= 8:
                    raise ValueError("too many output files")
                descriptor = os.open(
                    name, os.O_RDONLY | nofollow_flag | nonblock_flag, dir_fd=parent
                )
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError("output must be a regular file")
                    raw = stream.read(max_bytes - total + 1)
                total += len(raw)
                if total > max_bytes:
                    raise ValueError("output files exceed collection limit")
                path = os.path.normpath(os.path.join(directory, name))
                files.append(
                    {
                        "path": path,
                        "size": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "base64": base64.b64encode(raw).decode("ascii"),
                    }
                )
    finally:
        os.close(root_fd)
    return {
        "version": "execution.files/v1",
        "files": sorted(files, key=lambda item: str(item["path"])),
    }


def collection_json(max_bytes: int) -> str:
    return json.dumps(collect_output_files(max_bytes))
