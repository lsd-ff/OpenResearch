import pytest
from fastapi.testclient import TestClient

from openresearch.api.app import create_app
from openresearch.errors import ConflictError
from openresearch.models.runtime import Status
from openresearch.planner.code import build_code_plan
from openresearch.scheduler.worker import Worker


async def drain(worker) -> None:
    import asyncio

    for _ in range(20):
        await worker.tick()
        if worker.active:
            await asyncio.gather(*(active.future for active in worker.active.values()))
        else:
            return
    raise AssertionError("worker did not drain")


async def test_single_task_execution_does_not_run_descendants(
    settings, repository, files, sandbox
) -> None:
    plan = build_code_plan("smoke", "alice", "demo", settings)
    repository.save_plan(plan)
    with pytest.raises(ConflictError, match="dependencies"):
        repository.tasks.queue(plan.id, plan.nodes[1].id, "alice")
    assert repository.tasks.find_plan(plan.nodes[0].id, "alice") == plan.id
    worker = Worker(settings, repository, files, sandbox)
    repository.tasks.queue(plan.id, plan.nodes[0].id, "alice")
    await drain(worker)
    partial = repository.get_plan(plan.id)
    assert partial.status == Status.PENDING
    assert partial.nodes[0].status == Status.COMPLETED
    assert partial.nodes[1].status == Status.READY
    assert not sandbox.created
    repository.tasks.queue(plan.id, plan.nodes[1].id, "alice")
    await drain(worker)
    assert repository.get_plan(plan.id).status == Status.COMPLETED
    assert len(sandbox.created) == 2  # 实际执行之前还有独立只读版本探测。
    assert sorted(sandbox.created) == sorted(sandbox.closed)


def test_reassign_preserves_role_capabilities_and_owner(settings, repository, sandbox) -> None:
    with TestClient(create_app(settings, repository, sandbox)) as client:
        owner = {"X-User-Id": "alice"}
        plan = client.post("/api/plan", headers=owner, json={"intent": "smoke"}).json()[
            "plan_graph"
        ]
        task_id = plan["nodes"][0]["id"]
        url = f"/api/plans/{plan['id']}/tasks/{task_id}/reassign"
        assert (
            client.post(
                url, headers=owner, json={"assigned_to": "research_coding_agent"}
            ).status_code
            == 200
        )
        assert (
            client.post(url, headers=owner, json={"assigned_to": "librarian_agent"}).status_code
            == 422
        )
        assert (
            client.post(
                url, headers={"X-User-Id": "bob"}, json={"assigned_to": "coder_agent"}
            ).status_code
            == 404
        )
