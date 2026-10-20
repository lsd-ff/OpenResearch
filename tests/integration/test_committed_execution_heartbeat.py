import asyncio
import threading
from unittest.mock import AsyncMock

import pytest

from openresearch.models.runtime import Plan, Status, Task, TaskResult
from openresearch.scheduler.worker import Worker


@pytest.mark.parametrize("success", [True, False])
async def test_worker_does_not_cancel_an_execution_whose_result_is_already_committed(
    settings, repository, files, sandbox, monkeypatch, success
):
    task = Task(name="commit race", type="chat_answer", assigned_to="chat_agent")
    plan = Plan(
        user_intent="test commit boundary", owner_id="alice", session_id="commit", nodes=[task]
    )
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "alice")
    worker = Worker(settings, repository, files, sandbox)
    worker.executor._route = AsyncMock(
        return_value=TaskResult(success=success, error="" if success else "actual failure")
    )
    committed, release = threading.Event(), threading.Event()
    finish = repository.finish

    def delayed_return(lease, result):
        finish(lease, result)
        committed.set()
        assert release.wait(10), "test failed to release committed thread"

    monkeypatch.setattr(repository, "finish", delayed_return)
    try:
        await worker.tick()
        assert await asyncio.to_thread(committed.wait, 5)
        active = next(iter(worker.active.values()))
        assert repository.get_plan(plan.id).status == (
            Status.COMPLETED if success else Status.FAILED
        )
        await worker._renew()
        assert not active.future.cancelling(), (
            "committed result must not be mistaken for revoked work"
        )
        release.set()
        await asyncio.wait_for(active.future, 5)
        await worker._renew()
        assert not worker.active
    finally:
        release.set()
        await worker._shutdown()
