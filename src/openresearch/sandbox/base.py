from collections.abc import Awaitable, Callable
from typing import Protocol

from openresearch.models.resources import ManagedSandbox
from openresearch.models.runtime import ExecutionResult

LogCallback = Callable[[str, str], Awaitable[None]]


class Sandbox(Protocol):
    backend: str

    async def create(
        self, plan_id: str, execution_id: str, runtime_profile: str | None = None
    ) -> str: ...

    async def execute(self, sandbox_id: str, code: str, on_log: LogCallback) -> ExecutionResult: ...

    async def put_files(self, sandbox_id: str, files: dict[str, bytes]) -> None: ...

    async def get_file(self, sandbox_id: str, path: str, max_bytes: int) -> bytes: ...

    async def close(self, sandbox_id: str) -> None: ...

    async def inventory(self) -> list[ManagedSandbox]: ...

    async def image_identity(self, sandbox_id: str) -> str | None: ...

    async def health(self) -> bool: ...

    async def shutdown(self) -> None: ...
