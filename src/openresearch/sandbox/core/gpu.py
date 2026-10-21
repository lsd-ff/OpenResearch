from typing import Any

import docker

from openresearch.errors import ResourceBusyError

GPU_SLOT = "openresearch-gpu-slot"
ROLE_LABEL = "openresearch.core.role"
GPU_PROBE = "nvidia-smi --query-gpu=name --format=csv,noheader"


def device_environment(environment: dict[str, str], enabled: bool) -> dict[str, str]:
    # 镜像或快照中的 NVIDIA 环境不能绕过显式设备权限；CPU 分支必须在创建时屏蔽设备。
    values = {**environment, "NVIDIA_VISIBLE_DEVICES": "all" if enabled else "void"}
    if enabled:
        values.setdefault("NVIDIA_DRIVER_CAPABILITIES", "compute,utility")
    return values


class GPUReservation:
    """同一 Docker daemon 的 GPU 会话占位；重建工作容器期间不释放。"""

    def __init__(self, client: Any, labels: dict[str, str]) -> None:
        self.client, self.labels = client, labels
        self.id = ""

    def acquire(self, image_id: str) -> None:
        try:
            # 只创建、不启动控制容器。工作容器另行透传 GPU，故回滚可复用该占位。
            container = self.client.containers.create(
                image_id,
                ["true"],
                entrypoint=[],
                name=GPU_SLOT,
                environment={"NVIDIA_VISIBLE_DEVICES": "void"},
                labels={**self.labels, ROLE_LABEL: "gpu-slot"},
                network_mode="none",
                mem_limit=64 * 1024**2,
                nano_cpus=100_000_000,
                pids_limit=16,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                log_config=docker.types.LogConfig(type="none"),
            )
        except docker.errors.APIError as exc:
            if exc.status_code == 409:
                raise ResourceBusyError("GPU resource is held by another sandbox") from exc
            raise
        self.id = str(container.id)
