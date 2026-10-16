from datetime import timedelta

import pytest
from test_repository import queued_plan

from openresearch.config import Settings
from openresearch.errors import StaleExecutionError
from openresearch.models.execution import ExecutionLease, TaskCompletion
from openresearch.models.runtime import Status, utcnow
from openresearch.storage.repository import Repository


def expire_plan(repository: Repository, plan_id: str) -> None:
    with repository.database.transaction() as connection:
        plan = repository.database.load(connection, plan_id)
        plan.usage.started_at = utcnow() - timedelta(seconds=plan.budget.max_duration_seconds + 1)
        repository.database.save(connection, plan)


def test_global_limit_applies_across_plans(settings: Settings, repository: Repository) -> None:
    queued_plan(settings, repository)
    queued_plan(settings, repository)
    assert repository.claim_next("first", 30, 1) is not None
    assert repository.claim_next("second", 30, 1) is None


def test_expired_plan_releases_quota(settings: Settings, repository: Repository) -> None:
    plan_id = queued_plan(settings, repository)
    assert repository.claim_next("first", 30, 1) is not None
    expire_plan(repository, plan_id)
    assert repository.claim_next("second", 30, 1) is None
    assert repository.get_plan(plan_id).status == Status.CANCELED


def test_recovery_cannot_bypass_attempt_budget(settings: Settings, repository: Repository) -> None:
    settings.max_task_attempts = 1
    plan_id = queued_plan(settings, repository)
    assert repository.claim_next("first", 30, 1) is not None
    with repository.database.transaction() as connection:
        plan = repository.database.load(connection, plan_id)
        plan.nodes[0].lease_expires_at = utcnow() - timedelta(seconds=1)
        repository.database.save(connection, plan)
    assert repository.recover_expired() == 1
    assert repository.claim_next("second", 30, 1) is None
    finished = repository.get_plan(plan_id)
    assert finished.status == Status.CANCELED
    assert finished.usage.task_attempts == 1


def test_commit_cannot_bypass_duration_budget(settings: Settings, repository: Repository) -> None:
    plan_id = queued_plan(settings, repository)
    claimed = repository.claim_next("worker", 30, 1)
    assert claimed is not None
    lease = ExecutionLease.for_task(*claimed, "worker")
    expire_plan(repository, plan_id)
    with pytest.raises(StaleExecutionError):
        repository.finish(lease, TaskCompletion(error="late failure"))
    assert repository.get_plan(plan_id).status == Status.CANCELED
