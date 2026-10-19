import httpx

from openresearch.config import Settings
from openresearch.errors import SandboxError
from openresearch.models.resources import ManagedSandbox
from openresearch.models.runtime import ExecutionResult
from openresearch.sandbox import python_files
from openresearch.sandbox.base import LogCallback
from openresearch.sandbox.streams import stream_events


class HttpSandbox:
    """Compatibility adapter for scholar-agent/docker-sandbox's NDJSON API."""

    backend = "http"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url=settings.sandbox_url,
            headers={"Authorization": f"Bearer {settings.sandbox_token.get_secret_value()}"},
            timeout=httpx.Timeout(settings.task_timeout_seconds, connect=10),
            trust_env=False,
        )

    async def create(self, plan_id: str, execution_id: str) -> str:
        response = await self.client.post(
            "/api/v1/sandboxes",
            json={
                "image": self.settings.sandbox_image,
                "mount_path": "",
                "plan_id": plan_id,
                "execution_id": execution_id,
            },
        )
        response.raise_for_status()
        identifier = response.json().get("sandbox_id")
        if not isinstance(identifier, str) or not identifier.startswith(("dk-", "os-")):
            raise SandboxError("sandbox service returned an invalid identifier")
        return identifier

    async def execute(self, sandbox_id: str, code: str, on_log: LogCallback) -> ExecutionResult:
        final: ExecutionResult | None = None
        async with self.client.stream(
            "POST", f"/api/v1/sandboxes/{sandbox_id}/python/stream", json={"code": code}
        ) as response:
            response.raise_for_status()
            async for value in stream_events(response, self.settings.max_output_bytes * 3):
                if value.get("type") == "chunk":
                    await on_log(str(value.get("stream", "stdout")), str(value.get("message", "")))
                elif value.get("type") == "final":
                    if final is not None:
                        raise SandboxError("duplicate final sandbox event")
                    raw = value.get("response")
                    if not isinstance(raw, dict):
                        raise SandboxError("missing sandbox final response")
                    final = ExecutionResult.model_validate(
                        {key: raw[key] for key in ("stdout", "stderr", "exit_code") if key in raw}
                    )
                elif value.get("type") == "error":
                    raise SandboxError("remote sandbox execution failed")
        if final is None:
            raise SandboxError("sandbox stream ended without a final result")
        if len((final.stdout + final.stderr).encode("utf-8")) > self.settings.max_output_bytes:
            raise SandboxError("sandbox output exceeds configured size limit")
        return final

    async def close(self, sandbox_id: str) -> None:
        response = await self.client.delete(f"/api/v1/sandboxes/{sandbox_id}")
        if response.status_code != 404:
            response.raise_for_status()

    async def put_files(self, sandbox_id: str, files: dict[str, bytes]) -> None:
        await python_files.put_files(self.execute, sandbox_id, files)

    async def get_file(self, sandbox_id: str, path: str, max_bytes: int) -> bytes:
        return await python_files.get_file(self.execute, sandbox_id, path, max_bytes)

    async def health(self) -> bool:
        try:
            response = await self.client.get("/api/v1/health", timeout=3)
            response.raise_for_status()
            return response.json().get("ok") is True
        except (httpx.HTTPError, ValueError):
            return False

    async def inventory(self) -> list[ManagedSandbox]:
        response = await self.client.get("/api/v1/sandboxes")
        if response.status_code == 404:
            return []  # 兼容旧版 Go，只回收已登记资源；Python 服务提供命名空间清单。
        response.raise_for_status()
        return [ManagedSandbox(**item) for item in response.json()["sandboxes"]]

    async def image_identity(self, sandbox_id: str) -> str | None:
        response = await self.client.get(f"/api/v1/sandboxes/{sandbox_id}/image")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        value = response.json().get("image_identity")
        return value if isinstance(value, str) else None

    async def shutdown(self) -> None:
        await self.client.aclose()
