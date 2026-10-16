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
