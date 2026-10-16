from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from openresearch.config import Settings
from openresearch.errors import ConflictError, NotFoundError, StaleExecutionError
from openresearch.models.execution import ExecutionLease, TaskCompletion
from openresearch.models.runtime import Status, utcnow
from openresearch.planner.code import build_code_plan
from openresearch.storage.repository import Repository


def queued_plan(settings: Settings, repository: Repository) -> str:
    plan = build_code_plan("smoke", "alice", "session", settings)
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "alice")
    return plan.id


def test_state_and_events_survive_reopen(settings: Settings, repository: Repository) -> None:
    plan_id = queued_plan(settings, repository)
    reopened = Repository(settings.database_url)
    try:
        assert reopened.get_plan(plan_id, "alice").status == Status.READY
        assert [event.event_type for event in reopened.list_events(plan_id)] == [
            "plan_created",
            "plan_queued",
        ]
    finally:
        reopened.close()


def test_duplicate_execution_and_foreign_owner_are_rejected(
    settings: Settings, repository: Repository
) -> None:
    plan_id = queued_plan(settings, repository)
    with pytest.raises(ConflictError):
        repository.queue_plan(plan_id, "alice")
    with pytest.raises(NotFoundError):
        repository.get_plan(plan_id, "bob")


def test_concurrent_workers_cannot_claim_the_same_node(
    settings: Settings, repository: Repository
) -> None:
    queued_plan(settings, repository)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda name: repository.claim_next(name, 30, 2), ["a", "b"]))
    assert sum(result is not None for result in results) == 1


def test_cancel_rejects_late_results(settings: Settings, repository: Repository) -> None:
    plan_id = queued_plan(settings, repository)
    claimed = repository.claim_next("worker", 30, 2)
    assert claimed is not None
    _, task = claimed
    assert task.execution_id is not None
    repository.cancel(plan_id, "alice")
    with pytest.raises(StaleExecutionError):
        repository.finish(
            ExecutionLease(plan_id, task.id, task.execution_id, "worker"),
            TaskCompletion(error="late failure"),
        )
    assert repository.get_plan(plan_id).status == Status.CANCELED


def test_recovery_does_not_steal_live_lease(settings: Settings, repository: Repository) -> None:
    plan_id = queued_plan(settings, repository)
    claimed = repository.claim_next("worker", 30, 2)
    assert claimed is not None
    assert repository.recover_expired() == 0
    assert repository.get_plan(plan_id).nodes[0].status == Status.IN_PROGRESS


def test_expired_attempt_is_requeued_and_cannot_commit(
    settings: Settings, repository: Repository
) -> None:
    plan_id = queued_plan(settings, repository)
    claimed = repository.claim_next("worker", 30, 2)
    assert claimed is not None
    _, old_task = claimed
    assert old_task.execution_id is not None
    with repository.database.transaction() as connection:
        plan = repository.database.load(connection, plan_id)
        plan.nodes[0].lease_expires_at = utcnow() - timedelta(seconds=1)
        repository.database.save(connection, plan)
    assert repository.recover_expired() == 1
    reclaimed = repository.claim_next("new-worker", 30, 2)
    assert reclaimed is not None
    assert reclaimed[1].execution_id != old_task.execution_id
    with pytest.raises(StaleExecutionError):
        repository.finish(
            ExecutionLease(plan_id, old_task.id, old_task.execution_id, "worker"),
            TaskCompletion(error="late"),
        )


def test_failure_blocks_descendants_and_retry_resets_them(
    settings: Settings, repository: Repository
) -> None:
    plan_id = queued_plan(settings, repository)
    claimed = repository.claim_next("worker", 30, 2)
    assert claimed is not None
    _, task = claimed
    assert task.execution_id is not None
    repository.finish(
        ExecutionLease(plan_id, task.id, task.execution_id, "worker"),
        TaskCompletion(error="code preparation failed"),
    )
    failed = repository.get_plan(plan_id)
    assert failed.status == Status.FAILED
    assert failed.nodes[1].status == Status.BLOCKED
    repository.retry(plan_id, task.id, "alice")
    reset = repository.get_plan(plan_id)
    assert reset.status == Status.PENDING
    assert all(task.status == Status.PENDING for task in reset.nodes)
    assert reset.usage.task_attempts == 1
