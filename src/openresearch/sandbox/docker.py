import asyncio
import json
import time
from contextlib import closing
from dataclasses import dataclass
from importlib import resources
from typing import Any

import docker

from openresearch.config import Settings
from openresearch.errors import ResourceBusyError, SandboxError
from openresearch.models.compute import SandboxResources
from openresearch.models.resources import ManagedSandbox
from openresearch.models.runtime import ExecutionResult
from openresearch.sandbox import docker_files
from openresearch.sandbox.base import LogCallback
from openresearch.sandbox.core.transactions import settle
from openresearch.sandbox.namespace import runtime_namespace
from openresearch.sandbox.profiles import selected_profile
from openresearch.sandbox.workspaces import remove_workspace, workspace_volume


@dataclass
class DockerExecution:
    client: Any
    identifier: str
    stream: Any

    def close(self) -> None:
        try:
            self.stream.close()
        finally:
            self.client.close()


class DockerSandbox:
    """由 Python 直接管理 Docker，其他业务模块不持有 Docker 客户端。"""

    backend = "docker"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.namespace = runtime_namespace(settings.data_dir)

    def _create(self, plan_id: str, execution_id: str, runtime_profile: str | None = None) -> str:
        profile = selected_profile(self.settings, runtime_profile)
        with closing(docker.from_env(timeout=30)) as client:
            image = profile.image if profile else self.settings.sandbox_image
            if profile and profile.gpu:
                try:
                    client.containers.get("openresearch-gpu-slot")
                except docker.errors.NotFound:
                    pass
                else:
                    raise ResourceBusyError("GPU resource is held by another sandbox")
            try:
                client.images.get(image)
            except docker.errors.ImageNotFound:
                client.images.pull(image)
            labels = {
                "openresearch.managed": "true",
                "openresearch.plan_id": plan_id,
                "openresearch.execution_id": execution_id,
                "openresearch.namespace": self.namespace,
                "openresearch.created_at": str(time.time()),
                **({"openresearch.runtime_profile": runtime_profile} if runtime_profile else {}),
            }
            options: dict[str, Any] = dict(
                detach=True,
                labels=labels,
                mem_limit=f"{profile.memory_mb}m"
                if profile and profile.memory_mb
                else self.settings.sandbox_memory,
                nano_cpus=int(profile.cpus * 1_000_000_000) if profile else 1_000_000_000,
                device_requests=[docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])]
                if profile and profile.gpu
                else [],
                pids_limit=64,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                network_mode=self.settings.sandbox_network,
                read_only=True,
                tmpfs={
                    "/tmp": f"rw,noexec,nosuid,size={self.settings.sandbox_tmpfs_size_mb}m",
                    # 二进制 wheel 的扩展模块需要可执行映射；仅此内存盘允许映射。
                    "/workspace": (
                        f"rw,exec,nosuid,nodev,size={self.settings.sandbox_workspace_size_mb}m,mode=1777"
                    ),
                },
                user="65534:65534",
                environment={
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1",
                    # CPU 配额为一核，避免数值库线程池耗尽容器进程额度。
                    "OPENBLAS_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "1",
                    "MKL_NUM_THREADS": "1",
                    "NUMBA_NUM_THREADS": "1",
                    "OPENRESEARCH_PROBE_GPU": "1" if profile and profile.gpu else "0",
                },
            )
            volume_name = None
            container = None
            try:
                if profile and profile.gpu:
                    # 固定 Docker 名称在同一守护进程内原子占位，跨 Worker/服务实例均不可重叠。
                    options["name"] = "openresearch-gpu-slot"
                if profile and profile.workspace_storage == "volume":
                    volume_name = workspace_volume(client, image, labels)
                    options["tmpfs"].pop("/workspace")
                    options["volumes"] = {volume_name: {"bind": "/workspace", "mode": "rw"}}
                    labels["openresearch.workspace_volume"] = volume_name
                container = client.containers.create(image, ["sleep", "infinity"], **options)
                container.start()
                return str(container.id)
            except BaseException as exc:
                if container is not None:
                    container.remove(force=True)
                if volume_name:
                    remove_workspace(client, volume_name, self.namespace)
                if (
                    profile
                    and profile.gpu
                    and isinstance(exc, docker.errors.APIError)
                    and exc.status_code == 409
                ):
                    raise ResourceBusyError("GPU resource is held by another sandbox") from exc
                raise

    async def create(
        self, plan_id: str, execution_id: str, runtime_profile: str | None = None
    ) -> str:
        deadline = time.monotonic() + self.settings.gpu_wait_seconds
        while True:
            try:
                # 只限制占位等待；不可取消正在创建资源的 SDK 线程而丢失真实容器句柄。
                return await asyncio.to_thread(self._create, plan_id, execution_id, runtime_profile)
            except ResourceBusyError:
                if time.monotonic() >= deadline:
                    raise ResourceBusyError("GPU resource admission timed out") from None
                await asyncio.sleep(0.25)

    async def resources(self, sandbox_id: str) -> SandboxResources:
        async def discard(_: str, __: str) -> None:
            pass

        program = (
            resources.files("openresearch.sandbox.programs")
            .joinpath("resources.py")
            .read_text(encoding="utf-8")
        )
        result = await self.execute(sandbox_id, program, discard)
        if result.exit_code:
            raise SandboxError("sandbox resource probe failed: " + result.stderr[-1000:])

        def metadata() -> tuple[dict[str, Any], dict[str, Any]]:
            with closing(docker.from_env(timeout=10)) as client:
                return dict(client.info()), dict(client.containers.get(sandbox_id).attrs)

        engine, container = await asyncio.to_thread(metadata)
        raw = json.loads(result.stdout)
        limits = container["HostConfig"]
        raw["cpu_count"] = min(
            raw["cpu_count"],
            engine["NCPU"],
            limits.get("NanoCpus", 0) / 1_000_000_000 or engine["NCPU"],
        )
        raw["memory_mb"] = min(
            raw["memory_mb"],
            engine["MemTotal"] // 1048576,
            (limits.get("Memory") or engine["MemTotal"]) // 1048576,
        )
        raw["workspace_storage"] = (
            "volume"
            if container["Config"]["Labels"].get("openresearch.workspace_volume")
            else "tmpfs"
        )
        raw["container_image_id"] = container["Image"]
        return SandboxResources.model_validate(raw)

    async def put_files(self, sandbox_id: str, files: dict[str, bytes]) -> None:
        await asyncio.to_thread(docker_files.put_files, sandbox_id, files)

    async def get_file(self, sandbox_id: str, path: str, max_bytes: int) -> bytes:
        return await asyncio.to_thread(docker_files.get_file, sandbox_id, path, max_bytes)

    async def image_identity(self, sandbox_id: str) -> str:
        def inspect_image() -> str:
            with closing(docker.from_env(timeout=10)) as client:
                return str(client.containers.get(sandbox_id).attrs["Image"])

        return await asyncio.to_thread(inspect_image)

    async def execute(self, sandbox_id: str, code: str, on_log: LogCallback) -> ExecutionResult:
        opening = asyncio.create_task(asyncio.to_thread(self._open_execution, sandbox_id, code))
        session: DockerExecution | None = None
        try:
            try:
                session = await asyncio.shield(opening)
            except asyncio.CancelledError:
                # SDK 建立连接也在阻塞线程中，取消后仍需拿到连接并关闭。
                session = await opening
                raise
            output = await self._read_output(session.stream, on_log)
            inspection = await asyncio.to_thread(
                session.client.api.exec_inspect, session.identifier
            )
            exit_code = inspection.get("ExitCode")
            if not isinstance(exit_code, int):
                raise SandboxError("Docker did not return an exit code")
            return ExecutionResult(
                stdout=output["stdout"].decode("utf-8", errors="replace"),
                stderr=output["stderr"].decode("utf-8", errors="replace"),
                exit_code=exit_code,
            )
        finally:
            # 关闭输出连接唤醒阻塞的读取线程，随后执行工具回收容器并终止 exec。
            if session is not None:
                await asyncio.to_thread(session.close)

    @staticmethod
    def _open_execution(sandbox_id: str, code: str) -> DockerExecution:
        client = docker.from_env(timeout=10)
        try:
            execution = client.api.exec_create(
                sandbox_id, ["python", "-I", "-u", "-c", code], stdout=True, stderr=True
            )
            stream = client.api.exec_start(execution["Id"], stream=True, demux=True)
            return DockerExecution(client, str(execution["Id"]), stream)
        except BaseException:
            client.close()
            raise

    async def _read_output(self, stream: Any, on_log: LogCallback) -> dict[str, bytes]:
        output: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
        total = 0
        while True:
            chunk = await asyncio.to_thread(next, stream, None)
            if chunk is None:
                return {channel: b"".join(chunks) for channel, chunks in output.items()}
            for channel, raw in zip(("stdout", "stderr"), chunk, strict=True):
                if not raw:
                    continue
                total += len(raw)
                if total > self.settings.max_output_bytes:
                    raise SandboxError("sandbox output exceeds configured size limit")
                output[channel].append(raw)
                await on_log(channel, raw.decode("utf-8", errors="replace"))

    def _close(self, sandbox_id: str) -> None:
        with closing(docker.from_env(timeout=10)) as client:
            if sandbox_id.startswith("workspace-volume-"):
                remove_workspace(
                    client, sandbox_id.removeprefix("workspace-volume-"), self.namespace
                )
                return
            try:
                container = client.containers.get(sandbox_id)
            except docker.errors.NotFound:
                return
            if (
                container.labels.get("openresearch.managed") != "true"
                or container.labels.get("openresearch.namespace") != self.namespace
            ):
                raise SandboxError("refusing to remove an unmanaged container")
            volume_name = container.labels.get("openresearch.workspace_volume")
            self._remove_container(container)
            if volume_name:
                remove_workspace(client, volume_name, self.namespace)

    @staticmethod
    def _remove_container(container: Any) -> None:
        for attempt in range(2):
            try:
                container.remove(force=True)
                return
            except docker.errors.NotFound:
                return
            except docker.errors.APIError:
                raise
            except OSError:
                # requests 的传输异常继承 OSError；响应丢失不代表删除没有生效。
                # 只对已核验归属的不可变容器 ID 重试一次，404 也证明清理已完成。
                if attempt:
                    raise

    async def close(self, sandbox_id: str) -> None:
        # SDK 删除线程必须排空，再记录关闭/失败，避免取消后继续改变容器状态。
        await settle(asyncio.create_task(asyncio.to_thread(self._close, sandbox_id)))

    def _inventory(self) -> list[ManagedSandbox]:
        with closing(docker.from_env(timeout=10)) as client:
            containers = client.containers.list(
                all=True,
                filters={
                    "label": [
                        "openresearch.managed=true",
                        f"openresearch.namespace={self.namespace}",
                    ]
                },
            )
            managed = [
                ManagedSandbox(
                    str(container.id),
                    container.labels["openresearch.execution_id"],
                    float(container.labels["openresearch.created_at"]),
                )
                for container in containers
            ]
            for volume in client.volumes.list(
                filters={
                    "label": [
                        "openresearch.workspace=true",
                        f"openresearch.namespace={self.namespace}",
                    ]
                }
            ):
                labels = volume.attrs["Labels"]
                managed.append(
                    ManagedSandbox(
                        "workspace-volume-" + volume.name,
                        labels["openresearch.execution_id"],
                        float(labels["openresearch.created_at"]),
                    )
                )
            return managed

    async def inventory(self) -> list[ManagedSandbox]:
        return await asyncio.to_thread(self._inventory)

    async def health(self) -> bool:
        def ping() -> bool:
            with closing(docker.from_env(timeout=3)) as client:
                return bool(client.ping())

        try:
            return await asyncio.to_thread(ping)
        except docker.errors.DockerException:
            return False

    async def shutdown(self) -> None:
        pass  # 每次调用独立管理连接，没有需要全局关闭的连接池。
