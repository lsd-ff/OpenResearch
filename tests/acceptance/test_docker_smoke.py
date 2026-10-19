import os

import pytest

from openresearch.config import Settings
from openresearch.models.runtime import Status
from openresearch.planner.code import build_code_plan
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("OPENRESEARCH_TEST_DOCKER") != "1",
        reason="Docker tests require explicit opt-in",
    ),
]


async def test_real_python_experiment(
    settings: Settings, repository: Repository, files: ArtifactStore
) -> None:
    import asyncio
    import json

    sandbox = DockerSandbox(settings)
    plan = build_code_plan("smoke", "alice", "session", settings)
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "alice")
    worker = Worker(settings, repository, files, sandbox)
    try:
        async with asyncio.timeout(180):
            while repository.get_plan(plan.id).status not in {Status.COMPLETED, Status.FAILED}:
                await worker.tick()
                await asyncio.sleep(0.1)
        await asyncio.gather(*(active.future for active in worker.active.values()))
        finished = repository.get_plan(plan.id)
        assert finished.status == Status.COMPLETED, finished.nodes[-1].error
        run = json.loads(files.read(finished.artifacts["run_metrics"]))
        assert run["exit_code"] == 0
        assert json.loads(run["stdout"])["sum"] == 15
        assert repository.cleanup_candidates("docker") == []
    finally:
        for active in worker.active.values():
            if not active.future.done():
                active.future.cancel()
        await asyncio.gather(
            *(active.future for active in worker.active.values()), return_exceptions=True
        )
        await sandbox.shutdown()


async def test_configured_resource_budgets_preserve_container_isolation(settings):
    from contextlib import closing

    import docker

    settings.sandbox_memory = "1g"
    settings.sandbox_tmpfs_size_mb = 256
    settings.sandbox_workspace_size_mb = 512
    sandbox = DockerSandbox(settings)
    identifier = await sandbox.create("resource-verification", "bounded-memory")
    try:
        with closing(docker.from_env()) as client:
            config = client.containers.get(identifier).attrs["HostConfig"]
        assert config["Memory"] == 1024**3
        assert "size=256m" in config["Tmpfs"]["/tmp"]
        assert "noexec" in config["Tmpfs"]["/tmp"]
        assert "size=512m" in config["Tmpfs"]["/workspace"]
        assert config["ReadonlyRootfs"] and config["NetworkMode"] == "none"
        assert config["PidsLimit"] == 64 and config["NanoCpus"] == 1_000_000_000
    finally:
        await sandbox.close(identifier)
