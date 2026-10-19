import asyncio
import time
from contextlib import closing
from dataclasses import dataclass
from typing import Any

import docker

from openresearch.config import Settings
from openresearch.errors import SandboxError
from openresearch.models.resources import ManagedSandbox
from openresearch.models.runtime import ExecutionResult
from openresearch.sandbox import docker_files
from openresearch.sandbox.base import LogCallback
from openresearch.sandbox.namespace import runtime_namespace


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

    def _create(self, plan_id: str, execution_id: str) -> str:
        with closing(docker.from_env(timeout=30)) as client:
            container = client.containers.run(
                self.settings.sandbox_image,
                ["sleep", "infinity"],
                detach=True,
                labels={
                    "openresearch.managed": "true",
                    "openresearch.plan_id": plan_id,
                    "openresearch.execution_id": execution_id,
                    "openresearch.namespace": self.namespace,
                    "openresearch.created_at": str(time.time()),
                },
                mem_limit=self.settings.sandbox_memory,
                nano_cpus=1_000_000_000,
                pids_limit=64,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                network_mode=self.settings.sandbox_network,
                read_only=True,
                tmpfs={
                    "/tmp": "rw,noexec,nosuid,size=64m",
                    # 二进制 wheel 的扩展模块需要可执行映射；仅此内存盘允许映射。
                    "/workspace": "rw,nosuid,size=256m,mode=1777",
                },
                user="65534:65534",
                environment={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"},
            )
            return str(container.id)

    async def create(self, plan_id: str, execution_id: str) -> str:
        return await asyncio.to_thread(self._create, plan_id, execution_id)

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
            try:
                container = client.containers.get(sandbox_id)
            except docker.errors.NotFound:
                return
            if (
                container.labels.get("openresearch.managed") != "true"
                or container.labels.get("openresearch.namespace") != self.namespace
            ):
                raise SandboxError("refusing to remove an unmanaged container")
            container.remove(force=True)

    async def close(self, sandbox_id: str) -> None:
        await asyncio.to_thread(self._close, sandbox_id)

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
            return [
                ManagedSandbox(
                    str(container.id),
                    container.labels["openresearch.execution_id"],
                    float(container.labels["openresearch.created_at"]),
                )
                for container in containers
            ]

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
