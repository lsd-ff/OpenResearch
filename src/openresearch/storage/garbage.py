import re
import time
from contextlib import suppress
from pathlib import Path

from sqlalchemy import select

from openresearch.storage.database import Database
from openresearch.storage.schema import artifacts, attempts


class ArtifactGarbageCollector:
    def __init__(self, database: Database, root: Path, grace_seconds: int = 3600) -> None:
        self.database = database
        self.root = root.resolve()
        self.grace_seconds = grace_seconds

    def collect(self) -> int:
        removed = 0
        threshold = time.time() - self.grace_seconds
        for path in self.root.glob("*/*/*.txt"):
            if any(part.is_symlink() for part in (path, path.parent, path.parent.parent)):
                continue
            # 多个 Worker 可同时扫描；另一个 Worker 删除后直接略过。
            with suppress(FileNotFoundError):
                if path.stat().st_mtime <= threshold and self._collect_candidate(path):
                    removed += 1
        return removed

    def _collect_candidate(self, path: Path) -> bool:
        relative = path.relative_to(self.root)
        plan_id, execution_id, filename = relative.parts
        if not all(
            re.fullmatch(r"[a-f0-9]{32}", value)
            for value in (plan_id, execution_id, filename.removesuffix(".txt"))
        ):
            return False
        return self._remove_unreferenced(path, execution_id)

    def _remove_unreferenced(self, path: Path, execution_id: str) -> bool:
        # 与结果提交共用写锁；执行仍活跃或历史产物仍在索引中时禁止删除。
        with self.database.transaction() as connection:
            active = connection.execute(
                select(attempts.c.status).where(attempts.c.id == execution_id)
            ).scalar_one_or_none()
            if active == "in_progress":
                return False
            location = path.relative_to(self.root).as_posix()
            referenced = connection.execute(
                select(artifacts.c.id)
                .where(
                    artifacts.c.execution_id == execution_id,
                    artifacts.c.payload["location"].as_string() == location,
                )
                .limit(1)
            ).scalar_one_or_none()
            if referenced is not None:
                return False
            path.unlink(missing_ok=True)
            return True
