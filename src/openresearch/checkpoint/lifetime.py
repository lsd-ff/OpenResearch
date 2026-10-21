import threading
from contextlib import closing
from dataclasses import dataclass

import docker

from openresearch.errors import SandboxError

BASE_LABEL = "openresearch.core.base"


@dataclass
class _ImageState:
    labels: dict[str, str]
    parent: str | None
    references: int = 0
    owner_active: bool = True


class ImageReference:
    """同进程中的镜像引用；归还引用和重试物理清理分别计数。"""

    def __init__(self, owner: "SnapshotOwner", image_id: str) -> None:
        self.owner, self.image_id = owner, image_id
        self._held = True
        self._lock = threading.RLock()

    def clone(self) -> "ImageReference":
        with self._lock:
            if not self._held:
                raise SandboxError("snapshot lease is closed")
            return self.owner.acquire(self.image_id)

    def close(self) -> None:
        with self._lock:
            if self._held:
                self.owner.drop(self.image_id)
                self._held = False
            # 删除失败时保留待清理记录；重试不能再次递减同一个引用。
            self.owner.collect()


class SnapshotOwner:
    """跟踪会话、显式租约、活容器和派生镜像，不删除仍被使用的父镜像。"""

    def __init__(self) -> None:
        self._images: dict[str, _ImageState] = {}
        self._lock = threading.RLock()

    def register(self, image_id: str, labels: dict[str, str]) -> None:
        with self._lock:
            if image_id in self._images:
                return
            parent = labels.get(BASE_LABEL) or None
            if parent and parent not in self._images:
                raise SandboxError("snapshot ancestor is outside this ownership group")
            self._images[image_id] = _ImageState(labels.copy(), parent)
            if parent:
                self._images[parent].references += 1

    def acquire(
        self, image_id: str, expected_labels: dict[str, str] | None = None
    ) -> ImageReference:
        with self._lock:
            state = self._images.get(image_id)
            if state is None or (
                expected_labels
                and any(state.labels.get(key) != value for key, value in expected_labels.items())
            ):
                raise SandboxError("checkpoint does not belong to this core session")
            with closing(docker.from_env(timeout=30)) as client:
                image = client.images.get(image_id)
                if any(image.labels.get(key) != value for key, value in state.labels.items()):
                    raise SandboxError("snapshot ownership labels changed")
            state.references += 1
            return ImageReference(self, image_id)

    def drop(self, image_id: str) -> None:
        with self._lock:
            state = self._images[image_id]
            if state.references <= 0:
                raise SandboxError("snapshot reference underflow")
            state.references -= 1

    def retire(self, session_id: str) -> None:
        with self._lock:
            for state in self._images.values():
                if state.labels.get("openresearch.core.session") == session_id:
                    state.owner_active = False
            self.collect()

    def collect(self) -> None:
        with self._lock:
            eligible = [
                key
                for key, state in reversed(self._images.items())
                if not state.owner_active and state.references == 0
            ]
            if not eligible:
                return
            errors: list[Exception] = []
            with closing(docker.from_env(timeout=30)) as client:
                while eligible:
                    image_id = eligible.pop(0)
                    state = self._images[image_id]
                    try:
                        image = client.images.get(image_id)
                        if any(
                            image.labels.get(key) != value for key, value in state.labels.items()
                        ):
                            raise SandboxError("snapshot ownership labels changed")
                        client.images.remove(image_id)
                    except docker.errors.NotFound:
                        pass
                    except Exception as exc:
                        errors.append(exc)
                        continue
                    del self._images[image_id]
                    if state.parent:
                        parent = self._images[state.parent]
                        parent.references -= 1
                        if not parent.owner_active and parent.references == 0:
                            eligible.append(state.parent)
            if errors:
                raise SandboxError(
                    "snapshot cleanup failed: " + ", ".join(type(exc).__name__ for exc in errors)
                )
