import asyncio

import pytest

from openresearch.errors import ContractError
from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import Plan, Task, TaskContract
from openresearch.research.interpreter import interpreter_probe
from openresearch.sandbox.session import SandboxSessions


def owned_probe(settings, repository, sandbox, intent="run", allowed=True):
    task = Task(
        name="probe",
        type="code_execute",
        assigned_to="coder_agent",
        contract=TaskContract(allowed_tools=["runtime.version"] if allowed else []),
    )
    plan = Plan(user_intent=intent, owner_id="alice", session_id="probe", nodes=[task])
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "alice")
    claimed = repository.claim_next("probe-worker", 60, 2)
    lease = ExecutionLease.for_task(*claimed, "probe-worker")
    sessions = SandboxSessions(repository, sandbox)
    return interpreter_probe(settings, sessions, lease, task, intent)


async def test_legacy_permissions_do_not_create_version_probe_containers(
    settings, repository, sandbox
):
    probe = owned_probe(settings, repository, sandbox, allowed=False)
    assert probe is None and not sandbox.created


@pytest.mark.parametrize("intent", ["用Python==3.12.15运行", "use Python>=3.12.10,<3.13"])
async def test_actual_patch_must_satisfy_human_constraints_and_probe_is_cleaned(
    settings, repository, sandbox, intent
):
    probe = owned_probe(settings, repository, sandbox, intent)
    with pytest.raises(ContractError, match="requested Python"):
        await probe(None)
    assert sandbox.created and sandbox.closed == sandbox.created
    assert not repository.cleanup_candidates(sandbox.backend)


async def test_probe_is_cached_only_inside_its_own_execution_context(settings, repository, sandbox):
    first = owned_probe(settings, repository, sandbox)
    observed = await first(None)
    assert await first(None) == observed
    second = owned_probe(settings, repository, sandbox)
    assert await second(None) == observed
    assert len(sandbox.created) == 2 and sandbox.closed == sandbox.created


async def test_cancellation_reclaims_probe_container_under_the_original_lease(
    settings, repository, sandbox
):
    started = asyncio.Event()

    async def blocked(*arguments):
        started.set()
        await asyncio.Event().wait()

    sandbox.execute = blocked
    probe = owned_probe(settings, repository, sandbox)
    operation = asyncio.create_task(probe(None))
    await asyncio.wait_for(started.wait(), 2)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert sandbox.created and sandbox.closed == sandbox.created
    assert not repository.cleanup_candidates(sandbox.backend)
