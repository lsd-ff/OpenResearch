import json
import os
from unittest.mock import AsyncMock

import httpx
import pytest
from test_benchmark_preflight import COUNTED, DATA
from test_docker_smoke import pytestmark as pytestmark
from test_research import PREDICTOR, benchmark_plan, finish

from openresearch.models.chat import ExecutionSummary
from openresearch.models.planning import ExperimentDesign
from openresearch.models.runtime import Status
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.models.sources import EnvironmentSpec
from openresearch.sandbox.docker import DockerSandbox
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.http import HttpSandbox
from openresearch.sandbox.service import create_sandbox_app
from openresearch.scheduler.worker import Worker


def configure(settings):
    settings.runtime_profiles = {
        "cpu11": RuntimeProfile(image="python:3.11-slim-bookworm", python="3.11", memory_mb=512),
        "cpu13": RuntimeProfile(image="python:3.13-slim-bookworm", python="3.13", memory_mb=512),
    }


@pytest.mark.parametrize("http", [False, True])
async def test_parallel_research_plans_keep_actual_python_through_preflight_and_full_execution(
    settings, repository, files, http
):
    configure(settings)
    native = DockerSandbox(settings)
    sandbox = native
    if http:
        app = create_sandbox_app(settings, FallbackSandbox(native))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://runtime")
        sandbox = HttpSandbox(settings, client)
    worker = Worker(settings, repository, files, sandbox)
    plans = []
    for minor in (11, 13):
        code = (
            f"import sys\nassert sys.version_info[:2] == (3, {minor})\n"
            "print('ACTUAL_TARGET_PYTHON', sys.version.split()[0])\n"
            + PREDICTOR.replace("print('real predictions written')", COUNTED)
        )
        plan = benchmark_plan(
            settings,
            repository,
            code=code,
            ablations=False,
            data=DATA,
            intent=f"使用Python3.{minor}运行实际数据测试",
        )
        plans.append((minor, plan))
        repository.queue_plan(plan.id, plan.owner_id)
    try:
        for minor, plan in plans:
            await finish(worker, repository, plan.id)
            done = repository.get_plan(plan.id)
            assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
            report = json.loads(files.read(done.artifacts["research_report"]))
            assert report["runtime"]["profile"] == f"cpu{minor}"
            assert report["final_environment_lock"]["python"] == f"3.{minor}"
            preflight, full = report["preflight"]["run"], report["runs"][0]
            for run, count in ((preflight, 8), (full, 12)):
                assert run["environment"]["python"].startswith(f"3.{minor}.")
                assert run["attempts"][-1]["exit_code"] == 0
                assert f"ACTUAL_INPUT_COUNT {count}" in run["attempts"][-1]["stdout"]
                assert run["metrics"]["accuracy"] == 1.0
            assert preflight["attempts"][0]["sandbox_id"] != full["attempts"][0]["sandbox_id"]
        assert not repository.cleanup_candidates(sandbox.backend)
    finally:
        await worker._shutdown()


@pytest.mark.external
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="PyPI opt-in required")
@pytest.mark.parametrize("stage", ["resolve", "lock"])
async def test_public_python_requirement_recovers_to_registered_runtime_without_changing_package(
    settings, repository, files, stage
):
    configure(settings)
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    code = (
        "import anyenv, sys, importlib.metadata\n"
        "assert sys.version_info[:2] == (3, 13)\n"
        "assert importlib.metadata.version('anyenv') == '1.9.7'\n"
        "value = anyenv.load_json('{\"number\": 53}')\nassert value['number'] == 53\n"
        "print('ACTUAL_RUNTIME_RECOVERY', value['number'], anyenv.__file__, "
        "sys.version.split()[0])\n"
        + PREDICTOR.replace("print('real predictions written')", COUNTED)
    )
    if stage == "resolve":
        worker.executor.agents.coder.designer.llm.request_typed = AsyncMock(
            return_value=ExperimentDesign(
                dependencies=["anyenv==1.9.7"], rationale="public package"
            )
        )
        worker.executor.agents.agents["chat_agent"].llm.request_typed = AsyncMock(
            return_value=ExecutionSummary(response="实际执行结果见回执。")
        )
    preparation = (
        worker.executor.agents.coder.designer.environment
        if stage == "resolve"
        else worker.executor.agents.coder.environment.preparation
    )
    preparation.repairer.diagnose = AsyncMock(
        side_effect=AssertionError("fixed package must not be replaced with a different version")
    )
    plan = benchmark_plan(
        settings,
        repository,
        code=code,
        repairs=1,
        ablations=False,
        data=DATA,
        intent="保留 anyenv==1.9.7，运行真实数据测试",
        auto_design=stage == "resolve",
        environment=EnvironmentSpec(requirements=["anyenv==1.9.7"]) if stage == "lock" else None,
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id, timeout_seconds=300)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        report = json.loads(files.read(done.artifacts["research_report"]))
        key = (
            "dependency_resolution_report"
            if stage == "resolve"
            else "environment_preparation_report"
        )
        preparation = json.loads(files.read(done.artifacts[key]))
        assert len(preparation["attempts"]) == 2
        assert preparation["attempts"][0]["error"]
        assert preparation["attempts"][0]["phase"] == stage
        assert preparation["decisions"][0]["runtime_after"]["profile"] == "cpu13"
        assert preparation["decisions"][0]["proposal"] is None
        assert report["runtime"]["profile"] == "cpu13"
        assert (
            report["source_integrity"]["provided_code_sha256"]
            == report["source_integrity"]["prepared_entrypoint_sha256"]
        )
        for run, count in ((report["preflight"]["run"], 8), (report["runs"][0], 12)):
            assert run["environment"]["python"].startswith("3.13.")
            assert run["environment"]["installed"]["anyenv"] == "1.9.7"
            assert run["metrics"]["accuracy"] == 1.0
            assert "ACTUAL_RUNTIME_RECOVERY 53" in run["attempts"][-1]["stdout"]
            assert f"ACTUAL_INPUT_COUNT {count}" in run["attempts"][-1]["stdout"]
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()


@pytest.mark.external
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="PyPI opt-in required")
async def test_nondefault_runtime_preserves_its_target_during_missing_module_recovery(
    settings, repository, files
):
    configure(settings)
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    code = (
        "import yaml, yaml._yaml, sys\nassert sys.version_info[:2] == (3, 13)\n"
        "assert yaml.safe_load('number: 59')['number'] == 59\n"
        "print('ACTUAL_NATIVE_TARGET_RECOVERY', yaml._yaml.__file__)\n"
        + PREDICTOR.replace("print('real predictions written')", COUNTED)
    )
    plan = benchmark_plan(
        settings,
        repository,
        code=code,
        repairs=1,
        ablations=False,
        data=DATA,
        intent="用Python3.13及PyYAML==6.0.3运行数据测试",
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id, timeout_seconds=300)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        report = json.loads(files.read(done.artifacts["research_report"]))
        checked = report["preflight"]
        assert len(checked["environment_changes"]) == 1
        assert checked["environment_lock"]["python"] == "3.13"
        assert report["final_environment_lock"]["python"] == "3.13"
        assert report["runs"][0]["environment"]["installed"]["pyyaml"] == "6.0.3"
        assert "cpython-313" in report["runs"][0]["attempts"][-1]["stdout"]
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()
