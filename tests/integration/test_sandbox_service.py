import asyncio
import time

import httpx
import pytest
from conftest import FakeSandbox
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openresearch.errors import SandboxError
from openresearch.models.resources import ManagedSandbox
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.file_transfer import archive_files
from openresearch.sandbox.http import HttpSandbox
from openresearch.sandbox.service import create_sandbox_app


class InventorySandbox(FakeSandbox):
    async def inventory(self):
        return [
            ManagedSandbox(key, "b" * 32, time.time())
            for key in self.created
            if key not in self.closed
        ]


async def test_http_client_works_with_python_service_and_preserves_inventory_and_cleanup(settings):
    native = InventorySandbox()
    engine = FallbackSandbox(native)
    app = create_sandbox_app(settings, engine)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://service")
    adapter = HttpSandbox(settings, client)
    logs = []

    async def on_log(stream, text):
        logs.append(text)

    try:
        identifier = await adapter.create("a" * 32, "b" * 32)
        assert identifier.startswith("dk-")
        assert (await adapter.inventory())[0].id == identifier
        result = await adapter.execute(identifier, "print(1)", on_log)
        assert result.exit_code == 0 and result.stdout == "test-output"
        assert logs == ["test-output"]
        await adapter.close(identifier)
        assert await adapter.inventory() == []
        assert native.closed == native.created
    finally:
        await adapter.shutdown()


def test_sandbox_service_enforces_auth_image_and_namespace(settings):
    settings.sandbox_token = SecretStr("service-only-test-key")
    app = create_sandbox_app(settings, FallbackSandbox(InventorySandbox()))
    headers = {"Authorization": "Bearer service-only-test-key"}
    with TestClient(app) as client:
        assert client.post("/api/v1/sandboxes", json={}).status_code == 401
        assert (
            client.post(
                "/api/v1/sandboxes", json={"image": "unapproved"}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/sandboxes/dk-foreign/python/stream",
                json={"code": "print(1)"},
                headers=headers,
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/v1/sandboxes", json={"mount_path": "C:/"}, headers=headers
            ).status_code
            == 422
        )


def test_bulk_files_preserve_ownership_and_have_a_separate_bounded_body(settings):
    settings.source_max_bytes = settings.dependency_max_bytes = 1024
    app = create_sandbox_app(settings, FallbackSandbox(InventorySandbox()))
    raw = archive_files({"input.txt": b"x" * 3000})
    with TestClient(app) as client:
        identifier = client.post("/api/v1/sandboxes", json={}).json()["sandbox_id"]
        assert client.put("/api/v1/sandboxes/dk-foreign/files", content=raw).status_code == 404
        assert client.put(f"/api/v1/sandboxes/{identifier}/files", content=raw).status_code == 422
        assert (
            client.put(
                f"/api/v1/sandboxes/{identifier}/files", content=b"x" * (5 * 1024 * 1024)
            ).status_code
            == 413
        )
        assert (
            client.post(
                f"/api/v1/sandboxes/{identifier}/python", content=b"x" * (2 * 1024 * 1024)
            ).status_code
            == 413
        )


async def test_fallback_only_switches_on_creation_failure():
    class BrokenDocker(InventorySandbox):
        async def create(self, plan_id, execution_id):
            raise SandboxError("native Docker unavailable")

    fallback = InventorySandbox()
    adapter = FallbackSandbox(BrokenDocker(), fallback)
    identifier = await adapter.create("a" * 32, "b" * 32)
    assert identifier.startswith("os-") and len(fallback.created) == 1
    await adapter.close(identifier)
    assert fallback.closed == fallback.created


async def test_execution_failure_is_not_retried_in_another_backend():
    class FailedExecution(InventorySandbox):
        async def execute(self, sandbox_id, code, on_log):
            raise SandboxError("execution failed")

    secondary = InventorySandbox()
    adapter = FallbackSandbox(FailedExecution(), secondary)
    identifier = await adapter.create("a" * 32, "b" * 32)

    async def on_log(stream, text):
        pass

    with pytest.raises(SandboxError, match="execution failed"):
        await adapter.execute(identifier, "print(1)", on_log)
    assert secondary.created == []


async def test_healthy_native_backend_does_not_wait_for_unavailable_optional_service():
    class UnresponsiveFallback(InventorySandbox):
        async def health(self):
            await asyncio.Future()

    adapter = FallbackSandbox(InventorySandbox(), UnresponsiveFallback())
    async with asyncio.timeout(0.2):
        assert await adapter.health()
