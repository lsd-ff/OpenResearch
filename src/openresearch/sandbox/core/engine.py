import threading
import time
from typing import Any
from uuid import uuid4

import docker

from openresearch.checkpoint.lease import SnapshotLease
from openresearch.checkpoint.lifetime import BASE_LABEL, ImageReference, SnapshotOwner
from openresearch.checkpoint.registry import RollbackReceipt, Snapshot
from openresearch.errors import SandboxError
from openresearch.sandbox.core.cleanup import remove_owned_containers
from openresearch.sandbox.core.gpu import GPU_PROBE, ROLE_LABEL, GPUReservation, device_environment
from openresearch.sandbox.core.models import CommandResult, CoreConfig
from openresearch.sandbox.core.output import OutputCapture
from openresearch.sandbox.core.terminal import Terminal

STATE_PATH = "/opt/openresearch-core/state.sh"


class CoreEngine:
    def __init__(
        self, config: CoreConfig, namespace: str, source: SnapshotLease | None = None
    ) -> None:
        self.config, self.namespace = config, namespace
        self.client: Any = docker.from_env(timeout=60)
        self.session_id = uuid4().hex
        self.labels = {
            "openresearch.core.managed": "true",
            "openresearch.core.namespace": namespace,
            "openresearch.core.session": self.session_id,
        }
        self.container_id = ""
        self.terminal: Terminal | None = None
        self._source = source
        self._snapshots = source._reference.owner if source else SnapshotOwner()
        self._base: ImageReference | None = None
        self._references: list[ImageReference] = []
        self._gpu = GPUReservation(self.client, self.labels)

    def _image(self, image: str) -> Any:
        source = self.client.images.get(image)
        if source.attrs["Config"].get("Volumes"):
            raise SandboxError("core snapshots require an image without declared volumes")
        return source

    def spawn(self, image: str, restore: bool = False) -> tuple[str, Terminal]:
        source = self._image(image)
        if self.config.gpu_enabled and not self._gpu.id:
            raise SandboxError("GPU workload requires an owned reservation")
        command = ["/bin/bash", "--noprofile", "--norc", "--noediting", "-i"]
        if restore:
            command = ["/usr/bin/env", "-i", *command]
        container = self.client.containers.create(
            str(source.id),
            command,
            entrypoint=[],
            working_dir=self.config.working_dir,
            environment=device_environment(self.config.environment, self.config.gpu_enabled),
            stdin_open=True,
            tty=True,
            labels={**self.labels, ROLE_LABEL: "workload"},
            device_requests=[docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])]
            if self.config.gpu_enabled
            else [],
            runtime=self.config.runtime,
            network_mode=self.config.network,
            mem_limit=self.config.memory_bytes,
            nano_cpus=int(self.config.cpus * 1_000_000_000),
            pids_limit=128,
            security_opt=["no-new-privileges:true"],
            log_config=docker.types.LogConfig(type="none"),
        )
        terminal: Terminal | None = None
        try:
            # 在启动前 attach，既不依赖固定等待时间，也不丢失启动输出。
            stream = self.client.api.attach_socket(
                container.id, params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
            )
            terminal = Terminal(stream)
            container.start()
            # 启动时关闭 Readline，防止每个提示符恢复旧 termios 并将原始 LF 变成 CRLF。
            setup = (
                "stty -echo -opost -icanon min 1 time 0 </dev/tty && unset HISTFILE && PS1= && PS2="
            )
            if restore:
                setup += f" && builtin source {STATE_PATH}"
            with OutputCapture(self.config.data_dir, setup, self.config) as capture:
                result = terminal.execute(setup, capture, 10, threading.Event())
                if not result.succeeded:
                    raise SandboxError("core shell initialization failed")
            if self.config.gpu_enabled:
                with OutputCapture(self.config.data_dir, GPU_PROBE, self.config) as capture:
                    result = terminal.execute(GPU_PROBE, capture, 10, threading.Event())
                    if not result.succeeded or not result.output.strip():
                        raise SandboxError("core GPU device probe failed; no CPU fallback")
            return str(container.id), terminal
        except BaseException:
            try:
                if terminal:
                    terminal.close()
            finally:
                container.remove(force=True, v=True)
            raise

    def start(self) -> None:
        if self._source:
            self._base = self._source.borrow()
            self._references.append(self._base)
            image_id = self._base.image_id
        else:
            image = self._image(self.config.image)
            if image.labels.get("openresearch.core.managed") == "true":
                raise SandboxError("managed snapshot requires a live snapshot lease")
            image_id = str(image.id)
        if self.config.gpu_enabled:
            self._gpu.acquire(image_id)
        self.container_id, self.terminal = self.spawn(image_id, restore=self._source is not None)

    def execute(self, command: str, timeout: float, cancel: threading.Event) -> CommandResult:
        if self.terminal is None:
            raise SandboxError("core sandbox is not running")
        with OutputCapture(self.config.data_dir, command, self.config) as capture:
            return self.terminal.execute(command, capture, timeout, cancel)

    def commit(self, node_id: str) -> Snapshot:
        # Docker Commit 不保存进程内存。先把真实导出环境、函数和 cwd 写入容器可写层。
        command = (
            f"mkdir -p /opt/openresearch-core && builtin export -p > {STATE_PATH} && "
            f"builtin printf 'builtin cd -- %q\\n' \"$PWD\" >> {STATE_PATH} && "
            f"builtin declare -pf >> {STATE_PATH}"
        )
        if not self.execute(command, 10, threading.Event()).succeeded:
            raise SandboxError("cannot capture shell checkpoint state")
        labels = {
            **self.labels,
            "openresearch.core.node": node_id,
            BASE_LABEL: self._base.image_id if self._base else "",
        }
        response = self.client.api.commit(
            self.container_id,
            pause=True,
            message=f"OpenResearch checkpoint {node_id}",
            conf={"Labels": labels},
        )
        image_id = str(response["Id"])
        self._snapshots.register(image_id, labels)
        return Snapshot(node_id, image_id, time.time())

    def pin(self, snapshot: Snapshot) -> SnapshotLease:
        reference = self._snapshots.acquire(snapshot.image_id, self.labels)
        return SnapshotLease(snapshot, self.config, reference)

    def restore(self, snapshot: Snapshot) -> RollbackReceipt:
        image = self.client.images.get(snapshot.image_id)
        if any(image.labels.get(key) != value for key, value in self.labels.items()):
            raise SandboxError("checkpoint does not belong to this core session")
        # 新容器成功启动并恢复终端后才替换旧容器，启动失败不能先销毁可用状态。
        reference = self._snapshots.acquire(snapshot.image_id, self.labels)
        try:
            new_id, terminal = self.spawn(snapshot.image_id, restore=True)
        except BaseException:
            reference.close()
            raise
        self._references.append(reference)
        old_base = self._base
        old_id, old_terminal = self.container_id, self.terminal
        self.container_id, self.terminal = new_id, terminal
        self._base = reference
        try:
            if old_terminal:
                old_terminal.close()
        finally:
            self.client.containers.get(old_id).remove(force=True, v=True)
        if old_base:
            old_base.close()
            self._references.remove(old_base)
        return RollbackReceipt(old_id, new_id, snapshot.node_id, snapshot.image_id)

    def close(self) -> None:
        self._remove_owned()
        self.client.close()

    def _remove_owned(self) -> None:
        errors: list[Exception] = []
        if self.terminal:
            try:
                self.terminal.close()
            except Exception as exc:
                errors.append(exc)
            self.terminal = None
        filters = {"label": [f"{key}={value}" for key, value in self.labels.items()]}
        # 只清理本会话；查询也覆盖创建/提交成功但客户端未收到回执的资源。
        try:
            remove_owned_containers(self.client, self.labels)
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise SandboxError(
                "core cleanup failed: " + ", ".join(type(e).__name__ for e in errors)
            )
        # 容器全部移除后才能释放其基础镜像。查询也接管 Commit 成功但响应丢失的镜像。
        for image in self.client.images.list(all=True, filters=filters):
            self._snapshots.register(str(image.id), image.labels)
        self._snapshots.retire(self.session_id)
        for reference in self._references:
            reference.close()
        self._references.clear()
        self._base = None
        self.container_id = ""
        self._gpu.id = ""
