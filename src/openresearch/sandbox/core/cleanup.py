from typing import Any

import docker

from openresearch.errors import SandboxError
from openresearch.sandbox.core.gpu import ROLE_LABEL


def _remove_all(containers: list[Any]) -> None:
    errors: list[Exception] = []
    for container in containers:
        try:
            container.remove(force=True, v=True)
        except docker.errors.NotFound:
            pass
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise SandboxError(
            "core cleanup failed: " + ", ".join(type(exc).__name__ for exc in errors)
        )


def remove_owned_containers(client: Any, labels: dict[str, str]) -> None:
    filters = {"label": [f"{key}={value}" for key, value in labels.items()]}
    containers = client.containers.list(all=True, filters=filters)
    # 工作容器全部删除成功后才能释放占位；任何失败都保留同 daemon 的 GPU 互斥。
    _remove_all([item for item in containers if item.labels.get(ROLE_LABEL) != "gpu-slot"])
    _remove_all([item for item in containers if item.labels.get(ROLE_LABEL) == "gpu-slot"])
