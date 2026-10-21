from contextlib import suppress
from typing import Any

import docker

from openresearch.errors import SandboxError
from openresearch.models.runtime import new_id


def workspace_volume(client: Any, image: str, labels: dict[str, str]) -> str:
    name = "openresearch-workspace-" + new_id()
    volume = client.volumes.create(name=name, labels={**labels, "openresearch.workspace": "true"})
    initializer = None
    try:
        # 只为可信新卷设置非 root 写入权限；不运行实验或继承用户目录/网络。
        initializer = client.containers.create(
            image,
            ["chmod", "1777", "/workspace"],
            labels={**labels, "openresearch.workspace_volume": name},
            volumes={name: {"bind": "/workspace", "mode": "rw"}},
            network_mode="none",
            read_only=True,
            user="0:0",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit="128m",
            pids_limit=16,
        )
        initializer.start()
        if initializer.wait(timeout=10)["StatusCode"] != 0:
            raise SandboxError("workspace permission initialization failed")
        return str(volume.name)
    except BaseException:
        if initializer is not None:
            initializer.remove(force=True)
            initializer = None
        volume.remove()
        raise
    finally:
        if initializer is not None:
            initializer.remove(force=True)


def remove_workspace(client: Any, name: str, namespace: str) -> None:
    with suppress(docker.errors.NotFound):
        volume = client.volumes.get(name)
        labels = volume.attrs.get("Labels") or {}
        if (
            labels.get("openresearch.workspace") != "true"
            or labels.get("openresearch.namespace") != namespace
        ):
            raise SandboxError("refusing to remove an unmanaged workspace")
        volume.remove()
