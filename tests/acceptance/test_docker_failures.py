import asyncio
from contextlib import closing

import docker
import pytest
from requests.exceptions import ReadTimeout
from test_docker_smoke import pytestmark as pytestmark  # 同一套显式 Docker 开关。

from openresearch.config import Settings
from openresearch.models.runtime import Status
from openresearch.planner.code import build_code_plan
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


@pytest.mark.parametrize("deleted", [False, True])
async def test_actual_delete_response_loss_is_reconciled_without_leaving_a_container(
    settings, monkeypatch, deleted
):
    sandbox = DockerSandbox(settings)
    identifier = await sandbox.create("cleanup-acceptance", "response-loss")
    original = docker.models.containers.Container.remove
    calls = 0

    def remove(container, **kwargs):
        nonlocal calls
        if container.id == identifier:
            calls += 1
            if calls == 1:
                if deleted:
                    original(container, **kwargs)
                raise ReadTimeout("test-only lost response")
        return original(container, **kwargs)

    monkeypatch.setattr(docker.models.containers.Container, "remove", remove)
    try:
        await sandbox.close(identifier)
        assert calls == 2
        assert not await sandbox.inventory()
    finally:
        monkeypatch.setattr(docker.models.containers.Container, "remove", original)
        await sandbox.close(identifier)
        await sandbox.shutdown()


def containers_for(plan_id: str) -> list[str]:
    with closing(docker.from_env(timeout=5)) as client:
        return [
            container.id
            for container in client.containers.list(
                all=True, filters={"label": f"openresearch.plan_id={plan_id}"}
            )
        ]


@pytest.mark.parametrize("failure", ["timeout", "cancel", "output_limit", "nonzero"])
async def test_real_failure_reclaims_container(
    failure: str, settings: Settings, repository: Repository, files: ArtifactStore
) -> None:
    settings.max_output_bytes = 512
    code = 'import time\nprint("started", flush=True)\ntime.sleep(60)'
    if failure == "output_limit":
        code = 'print("x" * 10000)'
    if failure == "nonzero":
        code = 'raise RuntimeError("expected failure")'
    plan = build_code_plan("explicit test", "alice", "session", settings, code)
    if failure == "timeout":
        plan.nodes[1].timeout_seconds = 5
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "alice")
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    try:
        await drive_failure(worker, repository, plan.id, failure)
        finished = repository.get_plan(plan.id)
        assert finished.status == (Status.CANCELED if failure == "cancel" else Status.FAILED)
        if failure == "timeout":
            assert "timed out" in finished.nodes[1].error
        if failure == "output_limit":
            assert "size limit" in finished.nodes[1].error
        if failure == "nonzero":
            assert "expected failure" in finished.nodes[1].error
        assert repository.cleanup_candidates("docker") == []
        assert await asyncio.to_thread(containers_for, plan.id) == []
    finally:
        await worker._shutdown()


async def drive_failure(worker: Worker, repository: Repository, plan_id: str, failure: str) -> None:
    canceled = False
    async with asyncio.timeout(40):
        while True:
            await worker.tick()
            history = repository.list_events(plan_id)
            if (
                failure == "cancel"
                and not canceled
                and any(event.event_type == "task_log" for event in history)
            ):
                repository.cancel(plan_id, "alice")
                canceled = True
            if repository.get_plan(plan_id).status in {Status.FAILED, Status.CANCELED}:
                await worker.tick()
                await asyncio.gather(
                    *(active.future for active in worker.active.values()), return_exceptions=True
                )
                return
            await asyncio.sleep(0.05)
