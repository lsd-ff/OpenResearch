import asyncio
import json

import httpx
import pytest

from openresearch.api.app import create_app
from openresearch.models.runtime import Status
from openresearch.scheduler.worker import Worker


@pytest.fixture
async def legacy_client(settings, repository, files, sandbox):
    app = create_app(settings, repository, sandbox)
    worker = Worker(settings, repository, files, sandbox)

    async def drain():
        while True:
            await worker.tick()
            await asyncio.sleep(0.01)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
            headers={"X-User-Id": "alice", "X-Session-Id": "legacy"},
            timeout=10,
        ) as client,
    ):
        background = asyncio.create_task(drain())
        try:
            yield client
        finally:
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)
            await worker._shutdown()


def request(kind="execute_code", inputs=None):
    return {
        "task_id": "exec-1",
        "task_type": kind,
        "task_name": "Legacy request",
        "task_description": "运行原代码，不要自动修复",
        "assigned_to": "coder_agent",
        "inputs": {"generated_code": "assert 2 + 3 == 5\nprint(5)\n"} if inputs is None else inputs,
    }


def result(response):
    assert response.status_code == 200, response.text
    events = response.text.split("\n\n")
    selected = [event for event in events if event.startswith("event: result\n")]
    assert len(selected) == 1, response.text
    return json.loads(selected[0].split("data: ", 1)[1])


async def test_legacy_custom_id_executes_managed_plan_and_emits_go_fields(
    legacy_client, repository, sandbox
):
    payload = request()
    response = await legacy_client.post("/api/execute", json=payload)
    actual = result(response)
    plan = repository.get_plan(actual["plan_id"], "alice")
    assert plan.status == Status.COMPLETED
    assert plan.id != "exec-1" and all(node.id != "exec-1" for node in plan.nodes)
    assert plan.legacy.task_id == "exec-1" and plan.legacy.task_type == "execute_code"
    assert (
        actual["client_task_id"] == "exec-1"
        and actual["code"] == payload["inputs"]["generated_code"]
    )
    assert actual["image_base_64"] == actual["image_base64"] == ""
    assert actual["outputs"]["generated_code"] == actual["code"]
    assert response.headers["x-openresearch-plan-id"] == plan.id
    assert response.headers["x-openresearch-task-id"] == plan.legacy.native_task_id
    assert len(sandbox.created) == 2 and sandbox.closed == sandbox.created


async def test_legacy_generate_only_does_not_execute_experimental_code(
    legacy_client, repository, sandbox
):
    actual = result(await legacy_client.post("/api/execute", json=request("generate_code")))
    plan = repository.get_plan(actual["plan_id"], "alice")
    assert [node.type for node in plan.nodes] == ["code_generate"]
    assert actual["code"].startswith("assert") and not sandbox.created


async def test_legacy_approval_resumes_exact_plan_without_overriding_its_contract(
    legacy_client, settings, repository, sandbox
):
    settings.require_approval = True
    response = await legacy_client.post("/api/execute", json=request())
    assert response.status_code == 202 and response.json()["approval_required"]
    payload = response.json()
    assert not sandbox.created
    assert (await legacy_client.post(f"/api/plans/{payload['plan_id']}/approve")).status_code == 200
    replay = {
        "plan_id": payload["plan_id"],
        "task_id": payload["task_id"],
        "assigned_to": "untrusted-role",
        "inputs": {"generated_code": "raise AssertionError('INJECTED')"},
    }
    actual = result(await legacy_client.post("/api/execute", json=replay))
    assert "INJECTED" not in actual["code"]
    assert repository.get_plan(payload["plan_id"]).approval.status == "approved"
    again = result(await legacy_client.post("/api/execute", json=replay))
    assert again["plan_id"] == actual["plan_id"] and len(sandbox.created) == 2


async def test_legacy_environment_reference_is_owned_and_recreates_actual_attempts(
    legacy_client, repository, sandbox
):
    prepared = result(await legacy_client.post("/api/execute", json=request("prepare_runtime", {})))
    reference = prepared["outputs"]["runtime_session"]
    state = json.loads(prepared["outputs"]["legacy_environment_state"])
    assert state["lifecycle"] == "recreate_per_execution" and state["installation"] is None
    foreign = await legacy_client.post(
        "/api/execute",
        json=request(
            "install_dependencies", {"runtime_session": reference, "dependency_spec": "[]"}
        ),
        headers={"X-User-Id": "bob"},
    )
    assert foreign.status_code == 404
    installed = result(
        await legacy_client.post(
            "/api/execute",
            json=request(
                "install_dependencies", {"runtime_session": reference, "dependency_spec": "[]"}
            ),
        )
    )
    assert json.loads(installed["outputs"]["dependency_install_report"])["installed"] == {}
    payload = request()
    payload["inputs"]["prepared_runtime"] = installed["outputs"]["prepared_runtime"]
    actual = result(await legacy_client.post("/api/execute", json=payload))
    assert actual["code"].startswith("assert")
    assert all(
        repository.get_plan(identifier, "alice").status == Status.COMPLETED
        for identifier in (prepared["plan_id"], installed["plan_id"], actual["plan_id"])
    )
    assert sorted(sandbox.closed) == sorted(sandbox.created)


@pytest.mark.parametrize(
    "inputs",
    [
        {"runtime_session": "dk-unowned"},
        {"dataset_path": "C:/private/data.csv"},
        {"code": "print(1)", "generated_code": "print(2)"},
    ],
)
async def test_legacy_inputs_cannot_replace_resources_or_conflict_silently(legacy_client, inputs):
    response = await legacy_client.post("/api/execute", json=request(inputs=inputs))
    assert response.status_code == 422, response.text


async def test_legacy_data_profile_uses_owned_upload_and_go_image_alias(legacy_client):
    upload = await legacy_client.post(
        "/api/uploads", files={"file": ("data.csv", b"x,y\n1,yes\n2,\n", "text/csv")}
    )
    payload = request("dataset_profile", {"dataset_path": upload.json()["content_url"]})
    payload["assigned_to"] = "research_coding_agent"
    actual = result(await legacy_client.post("/api/execute", json=payload))
    assert actual["image_base_64"].startswith("data:image/svg+xml;base64,")
    assert json.loads(actual["structured_data"])["row_count"] == 2


@pytest.mark.parametrize("approval", [False, True])
async def test_first_legacy_response_preserves_anonymous_identity_for_recovery(
    legacy_client, settings, repository, approval
):
    settings.require_approval = approval
    legacy_client.headers.pop("X-User-Id")
    legacy_client.headers.pop("X-Session-Id")
    response = await legacy_client.post("/api/execute", json=request("generate_code"))
    assert "sa_uid=" in " ".join(response.headers.get_list("set-cookie"))
    assert "sa_sid=" in " ".join(response.headers.get_list("set-cookie"))
    if approval:
        selected = response.json()
        assert response.status_code == 202
        assert (
            await legacy_client.post(f"/api/plans/{selected['plan_id']}/approve")
        ).status_code == 200
        actual = result(
            await legacy_client.post(
                "/api/execute",
                json={"plan_id": selected["plan_id"], "task_id": selected["task_id"]},
            )
        )
    else:
        actual = result(response)
    fetched = await legacy_client.get("/api/plans/" + actual["plan_id"])
    assert fetched.status_code == 200
    assert repository.get_plan(actual["plan_id"]).owner_id == legacy_client.cookies["sa_uid"]
