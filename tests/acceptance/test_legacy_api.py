import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from test_docker_smoke import pytestmark as pytestmark

from openresearch.api.app import create_app
from openresearch.models.runtime import Status
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker
from openresearch.sources.dependency_resolution import DependencyResolver
from openresearch.sources.wheels import WheelSource


def payload(kind, inputs):
    return {
        "task_id": "exec-1",
        "task_name": "Legacy acceptance",
        "task_type": kind,
        "task_description": "按原代码运行全部断言，不要自动修复",
        "assigned_to": "coder_agent",
        "inputs": inputs,
    }


def completed_result(response):
    assert response.status_code == 200, response.text
    events = [event for event in response.text.split("\n\n") if event.startswith("event: result\n")]
    assert len(events) == 1, response.text
    return json.loads(events[0].split("data: ", 1)[1])


@pytest.fixture
async def legacy_docker(settings, repository, files):
    sandbox = DockerSandbox(settings)
    worker = Worker(settings, repository, files, sandbox)
    app = create_app(settings, repository, sandbox)

    async def drain():
        while True:
            await worker.tick()
            await asyncio.sleep(0.03)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://legacy",
            headers={"X-User-Id": "alice", "X-Session-Id": "legacy-actual"},
        ) as client,
    ):
        background = asyncio.create_task(drain())
        try:
            yield client, sandbox
        finally:
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)
            await worker._shutdown()


@pytest.mark.parametrize("profile", [None, "cpu13"])
async def test_real_legacy_prepare_install_execute_reuses_owned_frozen_configuration(
    legacy_docker, repository, monkeypatch, settings, profile
):
    client, sandbox = legacy_docker
    if profile:
        settings.runtime_profiles = {
            profile: RuntimeProfile(image="python:3.13-slim-bookworm", python="3.13")
        }

    async def forbidden(*args, **kwargs):
        raise AssertionError("prepare_runtime cannot resolve or download dependencies")

    async with asyncio.timeout(180):
        with monkeypatch.context() as patch:
            patch.setattr(DependencyResolver, "resolve", forbidden)
            patch.setattr(WheelSource, "fetch", forbidden)
            prepared = completed_result(
                await client.post(
                    "/api/execute",
                    json=payload(
                        "prepare_runtime", {"runtime_profile": profile} if profile else {}
                    ),
                )
            )
        state = json.loads(prepared["outputs"]["legacy_environment_state"])
        assert state["phase"] == "probe" and state["installation"] is None
        if profile:
            assert state["lock"]["python"] == "3.13"
        assert state["lock"]["interpreter"]["container_image_id"].startswith("sha256:")
        reference = prepared["outputs"]["runtime_session"]
        installed = completed_result(
            await client.post(
                "/api/execute",
                json=payload(
                    "install_dependencies", {"runtime_session": reference, "dependency_spec": "[]"}
                ),
            )
        )
        environment = json.loads(installed["outputs"]["dependency_install_report"])
        assert environment["python"] == state["lock"]["interpreter"]["python_full_version"]
        code = (
            "import sys\nvalues = [2, 3, 5, 7]\nassert sum(values) == 17\n"
            "print('ACTUAL_LEGACY', sum(values), sys.version.split()[0])\n"
        )
        executed = completed_result(
            await client.post(
                "/api/execute",
                json=payload(
                    "execute_code",
                    {
                        "generated_code": code,
                        "prepared_runtime": installed["outputs"]["prepared_runtime"],
                    },
                ),
            )
        )
        assert executed["code"] == code and "ACTUAL_LEGACY 17" in executed["result"]
        history = json.loads(executed["outputs"]["code_recovery_report"])
        assert history["success"] and len(history["attempts"]) == 1 and not history["decisions"]
        assert history["original_code_sha256"] == history["final_code_sha256"]
        assert history["attempts"][0]["environment"]["python"] == environment["python"]
        assert all(
            repository.get_plan(identifier, "alice").status == Status.COMPLETED
            for identifier in (prepared["plan_id"], installed["plan_id"], executed["plan_id"])
        )
    assert not await sandbox.inventory() and not repository.cleanup_candidates(sandbox.backend)


async def test_real_legacy_code_execution_returns_actual_go_image_field(legacy_docker):
    client, sandbox = legacy_docker
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
        '<text x="0" y="20">17</text></svg>'
    )
    code = (
        "from pathlib import Path\np = Path('/workspace/artifacts')\np.mkdir(parents=True)\n"
        f"(p / 'actual.svg').write_text({svg!r})\nprint('ACTUAL_SVG_WRITTEN')\n"
    )
    async with asyncio.timeout(90):
        actual = completed_result(
            await client.post("/api/execute", json=payload("code_execution", {"code": code}))
        )
    assert actual["image_base_64"] == actual["image_base64"]
    assert actual["image_base_64"].startswith("data:image/svg+xml;base64,")
    assert "ACTUAL_SVG_WRITTEN" in actual["result"] and actual["code"] == code
    assert not await sandbox.inventory()


@pytest.mark.gpu
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_GPU") != "1", reason="actual GPU opt-in required")
async def test_legacy_selected_gpu_executes_actual_cuda_kernel(legacy_docker, settings):
    client, sandbox = legacy_docker
    settings.runtime_profiles = {
        "cuda12": RuntimeProfile(
            image="python:3.12-slim-bookworm", python="3.12", gpu=True, memory_mb=512
        )
    }
    code = (Path(__file__).parents[1] / "fixtures/cuda_vector_add.py").read_text(encoding="utf-8")
    async with asyncio.timeout(120):
        actual = completed_result(
            await client.post(
                "/api/execute",
                json=payload("execute_code", {"code": code, "runtime_profile": "cuda12"}),
            )
        )
    observed = json.loads(actual["result"])
    assert observed["values"] == [11.0, 22.0, 33.0, 44.0] and observed["device"]
    report = json.loads(actual["outputs"]["code_recovery_report"])
    assert report["runtime"]["profile"] == "cuda12" and not report["decisions"]
    assert report["original_code_sha256"] == report["final_code_sha256"]
    assert not await sandbox.inventory()
