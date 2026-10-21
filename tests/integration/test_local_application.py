import asyncio
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openresearch.api.app import create_app
from openresearch.distribution import application, local_sandbox, resources
from openresearch.errors import ContractError
from openresearch.sandbox.service import create_sandbox_app


def configure(settings, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>actual workbench entry</html>", encoding="utf-8")
    (web / "asset.js").write_text("console.log('actual asset')", encoding="utf-8")
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
    return settings.model_copy(update={"port": port, "web_dir": web})


def test_workbench_spa_preserves_api_404_and_exact_assets(settings, tmp_path, sandbox):
    configuration = configure(settings, tmp_path)
    (configuration.web_dir / "api").mkdir()
    (configuration.web_dir / "api" / "existing.txt").write_text("must not be served")
    with TestClient(create_app(configuration, sandbox=sandbox)) as client:
        assert "actual workbench entry" in client.get("/workspace/history").text
        assert client.get("/asset.js").text == "console.log('actual asset')"
        assert client.get("/api/missing").status_code == 404
        assert client.get("/api").status_code == 404
        assert client.get("/api/existing.txt").status_code == 404


async def test_embedded_sandbox_real_http_auth_and_cleanup(settings, sandbox, monkeypatch):
    monkeypatch.setattr(
        local_sandbox, "create_sandbox_app", lambda cfg: create_sandbox_app(cfg, sandbox)
    )
    original = settings.model_dump()
    async with local_sandbox.local_backend(settings) as backend:
        assert backend.future is not None and not backend.future.done()
        assert backend.settings.sandbox_backend == "http"
        url = backend.settings.sandbox_url
        async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
            assert (await client.get("/api/v1/sandboxes")).status_code == 401
            response = await client.get(
                "/api/v1/sandboxes",
                headers={
                    "Authorization": "Bearer " + backend.settings.sandbox_token.get_secret_value(),
                },
            )
            assert response.json() == {"sandboxes": []}
        assert settings.model_dump() == original
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get(url + "/ping")


@pytest.mark.parametrize("backend", ["http", "opensandbox", "explicit_url"])
async def test_configured_external_service_is_not_replaced(settings, monkeypatch, backend):
    def unexpected(_):
        pytest.fail("an external backend must not start a local sandbox")

    monkeypatch.setattr(local_sandbox, "create_sandbox_app", unexpected)
    changes = (
        {"sandbox_url": "http://127.0.0.1:39123"}
        if backend == "explicit_url"
        else {"sandbox_backend": backend}
    )
    configured = settings.model_copy(update=changes)
    async with local_sandbox.local_backend(configured) as actual:
        assert actual.future is None
        assert actual.settings.sandbox_backend == ("http" if backend == "explicit_url" else backend)
        assert actual.settings.sandbox_url == configured.sandbox_url


async def ready(client):
    async with asyncio.timeout(10):
        while True:
            try:
                if (await client.get("/ping")).status_code == 200:
                    return
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.02)


async def test_integrated_worker_consumes_queue_and_closes_api(
    settings, tmp_path, sandbox, monkeypatch
):
    configured = configure(settings, tmp_path)
    # 此用例只验证 API/Worker 生命周期；执行替身不运行代码，发行物另用真实 Docker 验收。
    monkeypatch.setattr(application, "build_sandbox", lambda _: sandbox)
    monkeypatch.setattr(
        local_sandbox, "create_sandbox_app", lambda cfg: create_sandbox_app(cfg, sandbox)
    )
    stopping = asyncio.Event()
    future = asyncio.create_task(application.run_application(configured, stopping))
    url = f"http://127.0.0.1:{configured.port}"
    try:
        async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
            await ready(client)
            response = await client.post(
                "/api/plan",
                json={
                    "intent": "run explicit code without automatic repair",
                    "workflow": "code",
                    "code": "print(5)",
                },
            )
            identifier = response.json()["plan_graph"]["id"]
            assert (await client.post(f"/api/plans/{identifier}/execute")).status_code == 202
            async with asyncio.timeout(10):
                while True:
                    plan = (await client.get(f"/api/plans/{identifier}")).json()["plan_graph"]
                    if plan["status"] in {"completed", "failed"}:
                        break
                    await asyncio.sleep(0.02)
            assert plan["status"] == "completed", plan
            assert all(node["run_count"] == 1 for node in plan["nodes"])
            assert sandbox.created == sandbox.closed
    finally:
        stopping.set()
        await asyncio.wait_for(future, 15)
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get(url + "/ping")


async def test_worker_failure_stops_the_integrated_app(settings, tmp_path, sandbox, monkeypatch):
    configured = configure(settings, tmp_path)
    monkeypatch.setattr(
        local_sandbox, "create_sandbox_app", lambda cfg: create_sandbox_app(cfg, sandbox)
    )

    async def fail(_):
        await asyncio.sleep(0.05)
        raise RuntimeError("explicit worker failure fixture")

    monkeypatch.setattr(application.Worker, "run", fail)
    with pytest.raises(RuntimeError, match="component failure"):
        await asyncio.wait_for(application.run_application(configured), 15)
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get(f"http://127.0.0.1:{configured.port}/ping")


def test_frozen_resources_require_own_tool_and_keep_external_configuration(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    monkeypatch.setattr(resources, "BUNDLE", bundle)
    monkeypatch.setattr(resources.sys, "frozen", True, raising=False)
    monkeypatch.setattr(resources.sys, "executable", str(tmp_path / "openresearch.exe"))
    monkeypatch.delenv("OPENRESEARCH_DATA_DIR", raising=False)
    (tmp_path / ".env").write_text("OPENRESEARCH_PORT=39023\n", encoding="utf-8")
    settings = resources.load_settings()
    assert settings.port == 39023 and settings.data_dir == tmp_path / ".data"
    monkeypatch.setenv("OPENRESEARCH_DATA_DIR", str(tmp_path / "explicit-data"))
    assert resources.load_settings().data_dir == tmp_path / "explicit-data"
    with pytest.raises(ContractError, match="bundled uv"):
        resources.bundled_uv()
    with pytest.raises(ContractError, match=r"index\.html"):
        resources.web_root(settings)


async def test_busy_api_port_cleans_up_the_already_started_sandbox(settings, sandbox, monkeypatch):
    closed = []

    async def shutdown():
        closed.append(True)

    monkeypatch.setattr(sandbox, "shutdown", shutdown)
    monkeypatch.setattr(
        local_sandbox, "create_sandbox_app", lambda cfg: create_sandbox_app(cfg, sandbox)
    )
    with socket.create_server(("127.0.0.1", 0)) as occupied:
        configured = settings.model_copy(update={"port": occupied.getsockname()[1]})
        with pytest.raises(OSError):
            async with local_sandbox.local_backend(configured) as backend:
                await application.serve_application(backend, asyncio.Event())
    assert closed == [True]


async def test_api_startup_failure_is_a_failed_application(
    settings, tmp_path, sandbox, monkeypatch
):
    configured = configure(settings, tmp_path)

    @asynccontextmanager
    async def broken(_):
        raise RuntimeError("explicit API startup failure fixture")
        yield

    monkeypatch.setattr(application, "create_app", lambda _: FastAPI(lifespan=broken))
    monkeypatch.setattr(
        local_sandbox, "create_sandbox_app", lambda cfg: create_sandbox_app(cfg, sandbox)
    )
    with pytest.raises(RuntimeError, match="startup failed"):
        await asyncio.wait_for(application.run_application(configured), 15)
