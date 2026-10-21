import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from test_legacy_execution import legacy_client as legacy_client
from test_legacy_execution import result
from test_task_commands import drain

from openresearch.errors import ConflictError, NotFoundError
from openresearch.models.runtime import Status, Task, TaskContract, utcnow
from openresearch.planner.code import build_code_plan
from openresearch.scheduler.worker import Worker


def stage_payload(kind, inputs, role="research_coding_agent"):
    return {
        "task_id": "old-scientific-stage",
        "task_type": kind,
        "task_description": "按固定数据和原代码逐阶段执行，不要自动修复",
        "assigned_to": role,
        "inputs": inputs,
    }


async def adapter_request(client):
    upload = await client.post(
        "/api/uploads", files={"file": ("data.csv", b"id,x,y\na,1,yes\nb,0,no\n", "text/csv")}
    )
    return stage_payload(
        "benchmark_adapter_generate",
        {
            "code": "assert 3 + 4 == 7\nprint(7)\n",
            "research": {
                "max_repairs": 0,
                "benchmark": {
                    "upload_id": upload.json()["id"],
                    "input_columns": ["x"],
                    "target_column": "y",
                    "id_column": "id",
                },
            },
        },
    )


async def test_stage_queue_keeps_descendants_and_shared_start_time(
    settings, repository, files, sandbox
):
    plan = build_code_plan("运行原代码，不要自动修复", "alice", "stages", settings, "print(7)")
    future = Task(
        name="unrequested report",
        type="chat_answer",
        assigned_to="chat_agent",
        dependencies=[plan.nodes[-1].id],
        output_artifacts=["chat_answer"],
        contract=TaskContract(allowed_tools=["chat.answer"], output_artifacts=["chat_answer"]),
    )
    plan.nodes.append(future)
    repository.save_plan(plan)
    worker = Worker(settings, repository, files, sandbox)
    try:
        repository.tasks.queue_stage(plan.id, plan.nodes[0].id, "alice")
        started = repository.get_plan(plan.id).usage.started_at
        repository.tasks.queue_stage(plan.id, plan.nodes[0].id, "alice")
        with pytest.raises(ConflictError, match="another stage"):
            repository.tasks.queue_stage(plan.id, plan.nodes[1].id, "alice")
        await drain(worker)
        repository.tasks.queue_stage(plan.id, plan.nodes[1].id, "alice")
        assert repository.get_plan(plan.id).usage.started_at == started
        await drain(worker)
        partial = repository.get_plan(plan.id)
        assert partial.status == Status.PENDING and partial.execution_scope is None
        assert all(task.run_count == 1 for task in partial.nodes[:2])
        assert partial.nodes[-1].run_count == 0 and partial.nodes[-1].status == Status.READY
        count = partial.usage.task_attempts
        repository.tasks.queue_stage(plan.id, plan.nodes[1].id, "alice")
        assert repository.get_plan(plan.id).usage.task_attempts == count
        assert (
            sum(event.event_type == "stage_queued" for event in repository.list_events(plan.id))
            == 2
        )
        with pytest.raises(NotFoundError):
            repository.tasks.queue_stage(plan.id, plan.nodes[1].id, "bob")
    finally:
        await worker._shutdown()


async def test_old_adapter_generation_stops_before_preflight_and_preserves_complete_plan(
    legacy_client, repository, sandbox
):
    payload = await adapter_request(legacy_client)
    actual = result(await legacy_client.post("/api/execute", json=payload))
    partial = repository.get_plan(actual["plan_id"], "alice")
    assert partial.status == Status.PENDING and partial.legacy.staged
    assert len(partial.nodes) == 5 and partial.usage.task_attempts == 3
    assert actual["workflow_ref"] == "openresearch:" + partial.id
    assert actual["code"] == actual["outputs"]["generated_code"] == payload["inputs"]["code"]
    target = next(task for task in partial.nodes if task.id == actual["task_id"])
    assert target.type == "research_prepare" and target.status == Status.COMPLETED
    assert all(
        task.run_count == 0
        for task in partial.nodes
        if task.type in {"benchmark_preflight", "research_execute"}
    )
    assert len(sandbox.created) == 1  # 只有现有环境阶段的只读解释器探测，未执行预测程序。
    replay = result(await legacy_client.post("/api/execute", json={"task_id": target.id}))
    assert replay["outputs"] == actual["outputs"] and len(sandbox.created) == 1


@pytest.mark.parametrize("mutation", ["foreign", "code", "kind", "role", "raw_reference"])
async def test_scientific_continuation_rejects_foreign_sources_and_contract_changes(
    legacy_client, mutation
):
    actual = result(
        await legacy_client.post("/api/execute", json=await adapter_request(legacy_client))
    )
    inputs = {"workflow_ref": actual["workflow_ref"]}
    payload = stage_payload("benchmark_adapter_preflight", inputs)
    headers = {}
    if mutation == "foreign":
        headers = {"X-User-Id": "bob"}
    elif mutation == "code":
        inputs["code"] = "print('injected')"
    elif mutation == "kind":
        payload["task_type"] = "claim_rubric_extract"
        payload["assigned_to"] = "librarian_agent"
    elif mutation == "role":
        payload["assigned_to"] = "data_agent"
    else:
        inputs["workflow_ref"] = "dk-not-a-workflow"
    response = await legacy_client.post("/api/execute", json=payload, headers=headers)
    assert response.status_code == (404 if mutation == "foreign" else 422), response.text


async def test_staged_approval_resumes_only_selected_stage(
    legacy_client, settings, repository, sandbox
):
    settings.require_approval = True
    pending = await legacy_client.post("/api/execute", json=await adapter_request(legacy_client))
    assert pending.status_code == 202 and not sandbox.created
    selected = pending.json()
    assert (
        await legacy_client.post(f"/api/plans/{selected['plan_id']}/approve")
    ).status_code == 200
    actual = result(
        await legacy_client.post(
            "/api/execute",
            json={"plan_id": selected["plan_id"], "task_id": selected["task_id"]},
        )
    )
    partial = repository.get_plan(actual["plan_id"])
    assert partial.status == Status.PENDING and len(sandbox.created) == 1
    assert all(
        task.run_count == 0
        for task in partial.nodes
        if task.type in {"benchmark_preflight", "research_execute"}
    )


@pytest.mark.parametrize("budget", ["duration", "attempts"])
async def test_stage_continuation_cannot_reset_duration_or_attempt_budgets(
    legacy_client, settings, repository, sandbox, budget
):
    actual = result(
        await legacy_client.post("/api/execute", json=await adapter_request(legacy_client))
    )
    with repository.database.transaction() as connection:
        plan = repository.database.load(connection, actual["plan_id"])
        if budget == "duration":
            plan.usage.started_at = utcnow() - timedelta(
                seconds=plan.budget.max_duration_seconds + 1
            )
        else:
            plan.budget.max_task_attempts = plan.usage.task_attempts
        original = plan.usage.started_at
        repository.database.save(connection, plan)
    async with asyncio.timeout(10):
        failed = await legacy_client.post(
            "/api/execute",
            json=stage_payload(
                "benchmark_adapter_preflight",
                {"workflow_ref": actual["workflow_ref"]},
            ),
        )
    assert "event: error" in failed.text and "event: result" not in failed.text
    canceled = repository.get_plan(actual["plan_id"])
    assert canceled.status == Status.CANCELED and canceled.usage.started_at == original
    assert canceled.usage.task_attempts == 3 and len(sandbox.created) == 1


def test_concurrent_stage_requests_publish_one_queue_event(settings, repository):
    plan = build_code_plan("smoke", "alice", "concurrent-stage", settings)
    repository.save_plan(plan)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                lambda _: repository.tasks.queue_stage(plan.id, plan.nodes[0].id, "alice"),
                range(2),
            )
        )
    assert sum(event.event_type == "stage_queued" for event in repository.list_events(plan.id)) == 1
    assert repository.claim_next("first", 30, 2) is not None
    assert repository.claim_next("second", 30, 2) is None


@pytest.mark.parametrize("entry", ["plan", "single_task"])
async def test_switching_native_entry_does_not_reset_a_staged_plan_budget(
    legacy_client, repository, sandbox, entry
):
    actual = result(
        await legacy_client.post("/api/execute", json=await adapter_request(legacy_client))
    )
    with repository.database.transaction() as connection:
        plan = repository.database.load(connection, actual["plan_id"])
        plan.usage.started_at = utcnow() - timedelta(seconds=plan.budget.max_duration_seconds + 1)
        original = plan.usage.started_at
        target = next(task for task in plan.nodes if task.type == "benchmark_preflight")
        repository.database.save(connection, plan)
    path = (
        f"/api/plans/{actual['plan_id']}/execute"
        if entry == "plan"
        else f"/api/plans/{actual['plan_id']}/tasks/{target.id}/execute"
    )
    assert (await legacy_client.post(path)).status_code == 202
    async with asyncio.timeout(10):
        for _ in range(1000):
            if repository.get_plan(actual["plan_id"]).status == Status.CANCELED:
                break
            await asyncio.sleep(0.01)
    canceled = repository.get_plan(actual["plan_id"])
    assert canceled.usage.started_at == original and canceled.usage.task_attempts == 3
    assert len(sandbox.created) == 1
