import io
import tarfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from openresearch.config import Settings
from openresearch.models.runtime import ExecutionResult, new_id
from openresearch.sandbox.base import LogCallback
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


def make_repository_archive(name: str = "root/main.py", kind: bytes = tarfile.REGTYPE) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        item = tarfile.TarInfo(name)
        item.type = kind
        item.linkname = "../../etc/passwd"
        raw = b"print(1)"
        item.size = len(raw) if kind == tarfile.REGTYPE else 0
        archive.addfile(item, io.BytesIO(raw) if item.size else None)
    return stream.getvalue()


class FakeSandbox:
    """仅供离线测试的执行替身，不执行 Python 代码。"""

    backend = "test"

    def __init__(self) -> None:
        self.created: list[str] = []
        self.closed: list[str] = []
        self.files: dict[str, bytes] = {}

    async def create(self, plan_id: str, execution_id: str) -> str:
        identifier = new_id()
        self.created.append(identifier)
        return identifier

    async def execute(self, sandbox_id: str, code: str, on_log: LogCallback) -> ExecutionResult:
        if code.startswith("# OpenResearch artifact collector\n"):
            return ExecutionResult(
                stdout='{"version":"execution.files/v1","files":[]}', exit_code=0
            )
        await on_log("stdout", "test-output")
        return ExecutionResult(stdout="test-output", exit_code=0)

    async def close(self, sandbox_id: str) -> None:
        self.closed.append(sandbox_id)

    async def put_files(self, sandbox_id: str, files: dict[str, bytes]) -> None:
        self.files.update(files)

    async def get_file(self, sandbox_id: str, path: str, max_bytes: int) -> bytes:
        return self.files[path][:max_bytes]

    async def health(self) -> bool:
        return True

    async def inventory(self):
        return []

    async def image_identity(self, sandbox_id: str) -> str | None:
        return None

    async def shutdown(self) -> None:
        pass


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        poll_interval_seconds=0.01,
        llm_api_key="",
        llm_model="",
        api_token="",
    )


@pytest.fixture
def repository(settings: Settings) -> Iterator[Repository]:
    value = Repository(settings.database_url)
    yield value
    value.close()


@pytest.fixture
def files(settings: Settings) -> ArtifactStore:
    return ArtifactStore(settings.data_dir / "artifacts", settings.max_output_bytes * 3)


@pytest.fixture
def sandbox() -> FakeSandbox:
    return FakeSandbox()
