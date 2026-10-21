import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Snapshot:
    node_id: str
    image_id: str
    created_at: float


@dataclass(frozen=True)
class RollbackReceipt:
    old_container_id: str
    new_container_id: str
    node_id: str
    image_id: str


class SnapshotRegistry:
    def __init__(self) -> None:
        self._snapshots: dict[str, Snapshot] = {}
        self._lock = threading.RLock()

    def register(self, snapshot: Snapshot) -> None:
        with self._lock:
            self._snapshots[snapshot.node_id] = snapshot

    def get(self, node_id: str) -> Snapshot:
        with self._lock:
            return self._snapshots[node_id]

    def latest(self) -> Snapshot:
        with self._lock:
            if not self._snapshots:
                raise KeyError("no snapshots available")
            return next(reversed(self._snapshots.values()))

    def all(self) -> tuple[Snapshot, ...]:
        with self._lock:
            return tuple(self._snapshots.values())

    def restore(self, node_id: str) -> None:
        with self._lock:
            self.get(node_id)
            nodes = list(self._snapshots)
            for later in nodes[nodes.index(node_id) + 1 :]:
                del self._snapshots[later]
