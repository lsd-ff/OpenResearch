import asyncio
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from docker.utils import parse_bytes

from openresearch.config import Settings
from openresearch.errors import SandboxError
from openresearch.models.resources import ManagedSandbox
from openresearch.models.runtime import ExecutionResult
from openresearch.sandbox import python_files
from openresearch.sandbox.base import LogCallback
from openresearch.sandbox.namespace import runtime_namespace
from openresearch.sandbox.streams import stream_events


class OpenSandbox:
    """按官方 lifecycle v1 + execd SSE 协议接入，不依赖旧代理的私有接口。"""

    backend = "opensandbox"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.namespace = runtime_namespace(settings.data_dir)
        self.client = client or httpx.AsyncClient(
            base_url=settings.opensandbox_url.rstrip("/") + "/",
            headers={"OPEN-SANDBOX-API-KEY": settings.opensandbox_api_key.get_secret_value()},
            timeout=httpx.Timeout(settings.task_timeout_seconds, connect=10),
            trust_env=False,
        )

    @staticmethod
    def identifier(value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
            raise SandboxError("invalid OpenSandbox identifier")
        return value

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            if response.status_code == 204:
                return {}
            if len(response.content) > 2 * 1024 * 1024:
                raise SandboxError("OpenSandbox response exceeds size limit")
            value = response.json()
            if not isinstance(value, dict):
                raise SandboxError("invalid OpenSandbox response")
            return value
        except (httpx.HTTPError, ValueError):
            raise SandboxError("OpenSandbox service request failed") from None

    async def create(self, plan_id: str, execution_id: str) -> str:
        value = await self.request(
            "POST",
            "v1/sandboxes",
            json={
                "image": {"uri": self.settings.sandbox_image},
                "entrypoint": [
                    "sh",
                    "-c",
                    "mkdir -p /workspace && chmod 1777 /workspace && sleep infinity",
                ],
                "resourceLimits": {
                    "cpu": "1",
                    "memory": str(parse_bytes(self.settings.sandbox_memory)),
                },
                "timeout": self.settings.task_timeout_seconds + 120,
                "networkPolicy": {"defaultAction": "deny", "egress": []},
                "metadata": {
                    "openresearch.namespace": self.namespace,
                    "openresearch.plan_id": plan_id,
                    "openresearch.execution_id": execution_id,
                    "openresearch.created_at": str(time.time()),
                },
            },
        )
        identifier = self.identifier(str(value.get("id", "")))
        try:
            async with asyncio.timeout(60):
                while True:
                    state = await self.request("GET", f"v1/sandboxes/{identifier}")
                    phase = state.get("status", {}).get("state")
                    if phase == "Running":
                        return identifier
                    if phase in {"Failed", "Terminated", "Stopped"}:
                        raise SandboxError("OpenSandbox failed to become ready")
                    await asyncio.sleep(0.25)
        except BaseException:
            await self.close(identifier)
            raise

    async def endpoint(self, sandbox_id: str) -> tuple[str, dict[str, str]]:
        identifier = self.identifier(sandbox_id)
        value = await self.request(
            "GET",
            f"v1/sandboxes/{identifier}/endpoints/44772",
            params={"use_server_proxy": "true"},
        )
        endpoint = str(value.get("endpoint", ""))
        base = urlsplit(str(self.client.base_url))
        if "://" not in endpoint:
            endpoint = base.scheme + "://" + endpoint
        parts = urlsplit(endpoint)
        if (
            (parts.scheme, parts.netloc) != (base.scheme, base.netloc)
            or parts.query
            or parts.fragment
        ):
            raise SandboxError("OpenSandbox must provide a same-origin server proxy endpoint")
        headers = value.get("headers", {})
        if not isinstance(headers, dict) or any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in headers.items()
        ):
            raise SandboxError("invalid OpenSandbox endpoint headers")
        return endpoint.rstrip("/"), headers

    async def execute(self, sandbox_id: str, code: str, on_log: LogCallback) -> ExecutionResult:
        endpoint, headers = await self.endpoint(sandbox_id)
        output: dict[str, list[str]] = {"stdout": [], "stderr": []}
        completed = False
        failed = False
        execution_id = ""
        try:
            async with self.client.stream(
                "POST",
                endpoint + "/command",
                headers=headers,
                json={
                    "argv": ["python", "-u", "-c", code],
                    "background": False,
                    "timeout": self.settings.task_timeout_seconds * 1000,
                    "uid": 65534,
                    "gid": 65534,
                    "cwd": "/workspace",
                },
            ) as response:
                response.raise_for_status()
                async for event in stream_events(
                    response, self.settings.max_output_bytes * 3, sse=True
                ):
                    kind = event.get("type")
                    if not isinstance(kind, str):
                        raise SandboxError("invalid OpenSandbox event type")
                    if kind in output:
                        text = event.get("text", "")
                        if not isinstance(text, str):
                            raise SandboxError("invalid OpenSandbox output event")
                        output[kind].append(text)
                        await on_log(kind, text)
                    elif kind == "error":
                        failed = True
                    elif kind == "init":
                        execution_id = self.identifier(str(event.get("text", "")))
                    elif kind == "execution_complete":
                        if completed:
                            raise SandboxError("duplicate OpenSandbox completion event")
                        completed = True
        except httpx.HTTPError:
            raise SandboxError("OpenSandbox execution stream failed") from None
        if not completed or not execution_id:
            raise SandboxError("OpenSandbox stream ended without completion")
        stdout, stderr = "".join(output["stdout"]), "".join(output["stderr"])
        if len((stdout + stderr).encode()) > self.settings.max_output_bytes:
            raise SandboxError("sandbox output exceeds configured size limit")
        status = await self.request(
            "GET", endpoint + "/command/status/" + execution_id, headers=headers
        )
        exit_code = status.get("exit_code")
        if status.get("running") or type(exit_code) is not int:
            raise SandboxError("OpenSandbox did not return a terminal exit code")
        return ExecutionResult(
            stdout=stdout, stderr=stderr, exit_code=exit_code if exit_code else int(failed)
        )

    async def put_files(self, sandbox_id: str, files: dict[str, bytes]) -> None:
        await python_files.put_files(self.execute, sandbox_id, files)

    async def get_file(self, sandbox_id: str, path: str, max_bytes: int) -> bytes:
        return await python_files.get_file(self.execute, sandbox_id, path, max_bytes)

    async def close(self, sandbox_id: str) -> None:
        identifier = self.identifier(sandbox_id)
        response = await self.client.get(f"v1/sandboxes/{identifier}")
        if response.status_code == 404:
            return
        response.raise_for_status()
        if response.json().get("metadata", {}).get("openresearch.namespace") != self.namespace:
            raise SandboxError("refusing to remove an unmanaged OpenSandbox")
        await self.request("DELETE", f"v1/sandboxes/{identifier}")

    async def inventory(self) -> list[ManagedSandbox]:
        results: list[ManagedSandbox] = []
        for page in range(1, 101):
            value = await self.request(
                "GET",
                "v1/sandboxes",
                params={
                    "metadata": "openresearch.namespace=" + self.namespace,
                    "page": page,
                    "pageSize": 100,
                },
            )
            items = value.get("items")
            if not isinstance(items, list):
                raise SandboxError("invalid OpenSandbox inventory")
            for item in items:
                metadata = item.get("metadata", {})
                if metadata.get("openresearch.namespace") != self.namespace:
                    continue
                results.append(
                    ManagedSandbox(
                        self.identifier(str(item["id"])),
                        metadata["openresearch.execution_id"],
                        float(metadata["openresearch.created_at"]),
                    )
                )
            if len(items) < 100:
                return results
        raise SandboxError("OpenSandbox inventory exceeds configured page limit")

    async def image_identity(self, sandbox_id: str) -> str | None:
        return None  # Lifecycle 契约提供镜像名称，不保证实际运行镜像的内容摘要。

    async def health(self) -> bool:
        try:
            await self.request("GET", "v1/sandboxes", params={"pageSize": 1})
            return True
        except SandboxError:
            return False

    async def shutdown(self) -> None:
        await self.client.aclose()
