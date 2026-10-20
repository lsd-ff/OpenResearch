import asyncio
import json
import os
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock

import docker
import httpx
import pytest
from test_docker_smoke import pytestmark as pytestmark
from test_research import finish

from openresearch.errors import ResourceBusyError, SandboxError
from openresearch.models.chat import ExecutionSummary
from openresearch.models.compute import ReproductionRequest
from openresearch.models.context import Identity
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Status, new_id
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.code import build_code_plan
from openresearch.sandbox.docker import DockerSandbox
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.http import HttpSandbox
from openresearch.sandbox.service import create_sandbox_app
from openresearch.sandbox.workspaces import workspace_volume
from openresearch.scheduler.worker import Worker
from openresearch.services.plans import PlanService


def cuda_program():
    return "import os\nos.environ['CUDA_CACHE_DISABLE']='1'\n" + (
        Path(__file__).parents[1] / "fixtures/cuda_vector_add.py"
    ).read_text(encoding="utf-8")


async def test_actual_container_limits_and_disk_workspace_volume_are_reported_and_cleaned(settings):
    settings.runtime_profiles = {
        "disk": RuntimeProfile(
            image="python:3.12-slim-bookworm",
            python="3.12",
            cpus=0.5,
            memory_mb=192,
            workspace_storage="volume",
        ),
    }
    sandbox = DockerSandbox(settings)
    identifier = await sandbox.create(new_id(), new_id(), "disk")
    try:
        probe = await sandbox.resources(identifier)
        assert probe.python.startswith("3.12.") and probe.platform == "Linux"
        assert probe.cpu_count == 0.5 and 0 < probe.memory_mb <= 192
        assert probe.workspace_storage == "volume" and probe.workspace_free_mb > 1024
        assert not probe.gpus

        async def log(*args):
            pass

        result = await sandbox.execute(
            identifier,
            "from pathlib import Path\np=Path('/workspace/actual.txt')\n"
            "p.write_text('ACTUAL_DISK_83')\nprint(p.read_text())",
            log,
        )
        assert result.exit_code == 0 and result.stdout.strip() == "ACTUAL_DISK_83"
        assert len(await sandbox.inventory()) == 2
    finally:
        await sandbox.close(identifier)
        assert not await sandbox.inventory()
        await sandbox.shutdown()


async def test_failed_main_container_start_cleans_the_actual_volume_and_initializer(
    settings, monkeypatch
):
    settings.runtime_profiles = {
        "disk": RuntimeProfile(
            image="python:3.12-slim-bookworm", python="3.12", workspace_storage="volume"
        )
    }
    start = docker.models.containers.Container.start

    def fail_main(container, **kwargs):
        if container.attrs["Config"]["Cmd"] == ["sleep", "infinity"]:
            raise SandboxError("injected main start failure")
        return start(container, **kwargs)

    monkeypatch.setattr(docker.models.containers.Container, "start", fail_main)
    sandbox = DockerSandbox(settings)
    with pytest.raises(SandboxError, match="main start failure"):
        await sandbox.create(new_id(), new_id(), "disk")
    assert not await sandbox.inventory()
    await sandbox.shutdown()


async def test_orphan_workspace_inventory_recovers_its_own_volume_and_preserves_another_namespace(
    settings,
):
    first = DockerSandbox(settings)
    second = DockerSandbox(settings.model_copy(update={"data_dir": settings.data_dir / "other"}))
    names = []
    with closing(docker.from_env()) as client:
        for sandbox in (first, second):
            labels = {
                "openresearch.managed": "true",
                "openresearch.namespace": sandbox.namespace,
                "openresearch.execution_id": new_id(),
                "openresearch.created_at": str(time.time()),
            }
            names.append(workspace_volume(client, "python:3.12-slim-bookworm", labels))
    own, foreign = ("workspace-volume-" + name for name in names)
    try:
        assert [item.id for item in await first.inventory()] == [own]
        assert [item.id for item in await second.inventory()] == [foreign]
        with pytest.raises(SandboxError, match="unmanaged workspace"):
            await first.close(foreign)
        await first.close(own)
        assert not await first.inventory()
        assert [item.id for item in await second.inventory()] == [foreign]
    finally:
        await first.close(own)
        await second.close(foreign)
        await first.shutdown()
        await second.shutdown()


async def test_insufficient_full_resources_blocks_program_and_returns_actual_probe(
    settings, repository, files
):
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    worker.executor.agents.agents["chat_agent"].llm.request_typed = AsyncMock(
        return_value=ExecutionSummary(response="实际资源不足，未运行完整实验，详见资源报告。")
    )
    plan = PlanService(settings, repository).create(
        PlanSubmission(
            "完整复现所给实验",
            Identity("compute", "full-blocked"),
            workflow="reproduce",
            auto_design=True,
            code="raise AssertionError('FULL_PROGRAM_MUST_NOT_START')",
            research=ResearchSpec(reproduction=ReproductionRequest(mode="full", device="cpu")),
        )
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.FAILED
        decision = json.loads(files.read(done.artifacts["reproduction_decision"]))
        assert not decision["execution_allowed"] and not decision["full_eligible"]
        assert decision["requested_mode"] == "full" and decision["effective_mode"] == "smoke"
        assert decision["probe"]["cpu_count"] == 1 and decision["probe"]["memory_mb"] <= 1024
        assert decision["reasons"]
        assert all(
            node.run_count == 0
            for node in done.nodes
            if node.type in {"research_design", "research_execute"}
        )
        assert "research_report" not in done.artifacts and "chat_answer" in done.artifacts
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()


@pytest.mark.gpu
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_GPU") != "1", reason="real GPU opt-in required")
async def test_gpu_slot_is_exclusive_across_independent_services_and_released_after_close(settings):
    settings.runtime_profiles = {
        "cuda": RuntimeProfile(
            image="python:3.12-slim-bookworm", python="3.12", gpu=True, memory_mb=512
        ),
    }
    first = DockerSandbox(settings)
    app = create_sandbox_app(settings, FallbackSandbox(DockerSandbox(settings)))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://resource")
    second = HttpSandbox(settings, client)
    code = cuda_program()
    identifier = await first.create(new_id(), new_id(), "cuda")
    queued = asyncio.create_task(second.create(new_id(), new_id(), "cuda"))
    consumed = False

    async def log(*args):
        pass

    try:
        await asyncio.sleep(0.4)
        assert not queued.done()
        probe = await first.resources(identifier)
        assert probe.gpus and probe.gpus[0].memory_mb > 0 and not probe.gpu_error
        await first.close(identifier)
        identifier = None
        created = await asyncio.wait_for(asyncio.shield(queued), 30)
        consumed = True
        try:
            probe = await second.resources(created)
            assert probe.gpus and not probe.gpu_error
            result = await second.execute(created, code, log)
            assert result.exit_code == 0, result.stderr
            assert json.loads(result.stdout)["values"] == [11.0, 22.0, 33.0, 44.0]
        finally:
            await second.close(created)
        assert not await second.inventory()
    finally:
        if identifier:
            await first.close(identifier)
        if not consumed:
            outcome = (await asyncio.gather(queued, return_exceptions=True))[0]
            if isinstance(outcome, str):
                await second.close(outcome)
        await first.shutdown()
        await second.shutdown()


@pytest.mark.gpu
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_GPU") != "1", reason="real GPU opt-in required")
@pytest.mark.parametrize("mode", ["smoke", "full"])
async def test_gpu_request_runs_actual_kernel_through_the_research_plan(
    settings, repository, files, mode
):
    if mode == "full":
        # 只缩小管理员资源门槛以验收准入分支；小型 CUDA 程序不是完整论文训练。
        settings.reproduction_full_min_cpu = 1
        settings.reproduction_full_min_memory_mb = 256
        settings.reproduction_full_min_disk_mb = 64
    settings.runtime_profiles = {
        "cuda": RuntimeProfile(
            image="python:3.12-slim-bookworm",
            python="3.12",
            gpu=True,
            memory_mb=512,
            workspace_storage="volume" if mode == "full" else "tmpfs",
        ),
    }
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    worker.executor.agents.agents["chat_agent"].llm.request_typed = AsyncMock(
        return_value=ExecutionSummary(response="实际 GPU 程序已运行，输出见执行回执。")
    )
    code = cuda_program()
    plan = PlanService(settings, repository).create(
        PlanSubmission(
            "用GPU运行原程序",
            Identity("compute", "gpu-plan"),
            workflow="reproduce",
            code=code,
            research=ResearchSpec(
                max_repairs=0, reproduction=ReproductionRequest(mode=mode, device="gpu")
            ),
        )
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        report = json.loads(files.read(done.artifacts["research_report"]))
        run = report["runs"][0]
        assert (
            report["reproduction"]["execution_allowed"] and report["reproduction"]["probe"]["gpus"]
        )
        assert (
            run["attempts"][0]["exit_code"] == 0 and run["reproduction"]["effective_mode"] == mode
        )
        assert json.loads(run["attempts"][0]["stdout"])["values"] == [11.0, 22.0, 33.0, 44.0]
        assert (
            report["source_integrity"]["provided_code_sha256"]
            == report["source_integrity"]["prepared_entrypoint_sha256"]
        )
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()


@pytest.mark.gpu
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_GPU") != "1", reason="real GPU opt-in required")
async def test_gpu_wait_deadline_preserves_the_other_owner_and_never_uses_fallback(settings):
    settings.gpu_wait_seconds = 1
    settings.runtime_profiles = {
        "cuda": RuntimeProfile(image="python:3.12-slim-bookworm", python="3.12", gpu=True)
    }
    first = DockerSandbox(settings)
    second_settings = settings.model_copy(update={"data_dir": settings.data_dir / "another"})
    second = DockerSandbox(second_settings)
    identifier = await first.create(new_id(), new_id(), "cuda")
    unused = AsyncMock()
    try:
        with pytest.raises(ResourceBusyError, match="admission timed out"):
            await FallbackSandbox(second, unused).create(new_id(), new_id(), "cuda")
        unused.create.assert_not_called()
        assert not await second.inventory()
        probe = await first.resources(identifier)
        assert probe.gpus and not probe.gpu_error
    finally:
        await first.close(identifier)
        await first.shutdown()
        await second.shutdown()


@pytest.mark.gpu
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_GPU") != "1", reason="real GPU opt-in required")
async def test_code_workflow_can_select_gpu_and_run_actual_kernel(settings, repository, files):
    settings.runtime_profiles = {
        "cuda": RuntimeProfile(
            image="python:3.12-slim-bookworm", python="3.12", gpu=True, memory_mb=512
        ),
    }
    code = cuda_program()
    plan = build_code_plan("用GPU运行原程序", "compute", "gpu-code", settings, code)
    repository.save_plan(plan)
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    worker.executor.agents.agents["chat_agent"].llm.request_typed = AsyncMock(
        return_value=ExecutionSummary(response="实际 GPU 程序已运行，输出见执行回执。")
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        report = json.loads(files.read(done.artifacts["code_recovery_report"]))
        attempt = report["attempts"][0]
        assert attempt["output"]["exit_code"] == 0 and attempt["reproduction"]["probe"]["gpus"]
        assert json.loads(attempt["output"]["stdout"])["values"] == [11.0, 22.0, 33.0, 44.0]
        assert attempt["code"] == code and report["success"]
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()


async def test_full_program_is_readmitted_after_probe_and_stops_when_workspace_fills(
    settings, repository, files
):
    class DiskPressureSandbox(DockerSandbox):
        probes = 0

        async def resources(self, identifier):
            self.probes += 1
            if self.probes == 2:

                async def log(*args):
                    pass

                output = await self.execute(
                    identifier,
                    "with open('/workspace/pressure', 'wb') as f: f.write(b'x' * (64 * 1048576))",
                    log,
                )
                assert output.exit_code == 0, output.stderr
            return await super().resources(identifier)

    settings.sandbox_workspace_size_mb = 128
    settings.reproduction_full_min_cpu = 1
    settings.reproduction_full_min_memory_mb = 64
    settings.reproduction_full_min_disk_mb = 96
    settings.reproduction_full_min_gpu = 0
    sandbox = DiskPressureSandbox(settings)
    worker = Worker(settings, repository, files, sandbox)
    worker.executor.agents.agents["chat_agent"].llm.request_typed = AsyncMock(
        return_value=ExecutionSummary(response="执行前实际磁盘空间不足，完整程序未执行。")
    )
    plan = PlanService(settings, repository).create(
        PlanSubmission(
            "完整复现所给CPU程序",
            Identity("compute", "space-changed"),
            workflow="reproduce",
            code="raise AssertionError('FULL_PROGRAM_MUST_NOT_START')",
            research=ResearchSpec(reproduction=ReproductionRequest(mode="full", device="cpu")),
        )
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.FAILED
        initial = json.loads(files.read(done.artifacts["reproduction_decision"]))
        assert initial["execution_allowed"] and initial["effective_mode"] == "full"
        report = json.loads(files.read(done.artifacts["research_report"]))
        run = report["runs"][0]
        assert run["attempts"][0]["phase"] == "resources"
        assert run["attempts"][0]["exit_code"] is None
        assert not run["reproduction"]["execution_allowed"]
        assert run["reproduction"]["probe"]["workspace_free_mb"] < 96
        assert sandbox.probes == 2 and not await sandbox.inventory()
    finally:
        await worker._shutdown()
