import pytest
from conftest import FakeSandbox
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openresearch.api.app import create_app
from openresearch.config import Settings
from openresearch.storage.repository import Repository


def test_negative_sse_cursor_is_rejected(
    settings: Settings, repository: Repository, sandbox: FakeSandbox
) -> None:
    with TestClient(create_app(settings, repository, sandbox)) as client:
        plan = client.post("/api/plan", json={"intent": "smoke"}).json()["plan_graph"]
        response = client.get(f"/api/plans/{plan['id']}/stream", headers={"Last-Event-ID": "-1"})
        assert response.status_code == 400


def test_authentication_error_includes_cors_headers(
    settings: Settings, repository: Repository, sandbox: FakeSandbox
) -> None:
    settings.api_token = SecretStr("test-secret")
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/plan", json={"intent": "smoke"}, headers={"Origin": "http://localhost:5173"}
        )
        assert response.status_code == 401
        assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"


def test_frontend_plan_contract_and_ownership(
    settings: Settings, repository: Repository, sandbox: FakeSandbox
) -> None:
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/plan", json={"intent": "smoke"}, headers={"X-User-Id": "alice"}
        )
        assert response.status_code == 200
        graph = response.json()["plan_graph"]
        assert graph["nodes"][0]["assigned_to"] == "coder_agent"
        assert graph["edges"][0]["from"] == graph["nodes"][0]["id"]
        plan_id = graph["id"]
        assert client.get(f"/api/plans/{plan_id}", headers={"X-User-Id": "bob"}).status_code == 404
        assert (
            client.post(f"/api/plans/{plan_id}/execute", headers={"X-User-Id": "alice"}).status_code
            == 202
        )
        assert (
            client.post(f"/api/plans/{plan_id}/execute", headers={"X-User-Id": "alice"}).status_code
            == 409
        )


def test_auth_cookie_covers_rest_and_sse(
    settings: Settings, repository: Repository, sandbox: FakeSandbox
) -> None:
    settings.api_token = SecretStr("test-token")
    with TestClient(create_app(settings, repository, sandbox)) as client:
        assert client.get("/api/hello").status_code == 401
        assert client.post("/api/plan", json={"intent": "smoke"}).status_code == 401
        assert (
            client.post(
                "/api/auth/session", headers={"Authorization": "Bearer test-token"}
            ).status_code
            == 200
        )
        hello = client.get("/api/hello")
        assert hello.text == "hello api group"
        assert hello.headers["Content-Type"].startswith("text/plain")
        response = client.post("/api/plan", json={"intent": "smoke"})
        assert response.status_code == 200
        plan_id = response.json()["plan_graph"]["id"]
        client.post(f"/api/plans/{plan_id}/cancel")
        response = client.get(f"/api/plans/{plan_id}/stream")
        assert response.status_code == 200
        assert "event: plan_event" in response.text
        assert "plan_canceled" in response.text


def test_sse_cursor_does_not_repeat_events(
    settings: Settings, repository: Repository, sandbox: FakeSandbox
) -> None:
    with TestClient(create_app(settings, repository, sandbox)) as client:
        plan_id = client.post("/api/plan", json={"intent": "smoke"}).json()["plan_graph"]["id"]
        client.post(f"/api/plans/{plan_id}/cancel")
        first = repository.list_events(plan_id)[0].sequence
        stream = client.get(f"/api/plans/{plan_id}/stream", headers={"Last-Event-ID": str(first)})
        assert "plan_created" not in stream.text
        assert "plan_canceled" in stream.text


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("success", [False, True])
def test_sse_drains_events_committed_between_history_and_terminal_snapshot(
    settings, repository, sandbox, monkeypatch, direct, success
):
    """真实事务在两次读取之间结束，不能让终态快照截断尚未读取的尾部事件。"""
    from openresearch.models.execution import ExecutionLease, TaskCompletion
    from openresearch.models.runtime import Plan, Task, TaskContract, TaskResult

    task = Task(
        name="stream race",
        type="data_analyze",
        assigned_to="data_agent",
        contract=TaskContract(allowed_tools=["data.profile"]),
    )
    plan = Plan(user_intent="stream race", owner_id="guest", session_id="race", nodes=[task])
    repository.save_plan(plan)
    if not direct:
        repository.queue_plan(plan.id, plan.owner_id)
    original = repository.list_events
    completed = False

    def finish_after_read(plan_id, after=0):
        nonlocal completed
        history = original(plan_id, after)
        if not completed:
            completed = True
            claimed = repository.claim_next("stream-test", 30, 2)
            assert claimed is not None
            lease = ExecutionLease.for_task(*claimed, "stream-test")
            repository.append_log(lease, "ACTUAL_COMMITTED_LATE_LOG")
            repository.finish(
                lease,
                TaskCompletion(
                    result=TaskResult(
                        success=success,
                        error="" if success else "ACTUAL_COMMITTED_FAILURE",
                        result="ACTUAL_COMMITTED_RESULT",
                        image_base64="ACTUAL_COMMITTED_CHART",
                    )
                ),
            )
        return history

    monkeypatch.setattr(repository, "list_events", finish_after_read)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = (
            client.post(
                "/api/execute",
                json={"plan_id": plan.id, "task_id": task.id},
                headers={"X-User-Id": "guest"},
            )
            if direct
            else client.get(f"/api/plans/{plan.id}/stream", headers={"X-User-Id": "guest"})
        )
    assert response.status_code == 200
    if success:
        assert "ACTUAL_COMMITTED_RESULT" in response.text
        assert "ACTUAL_COMMITTED_CHART" in response.text
    else:
        assert "ACTUAL_COMMITTED_FAILURE" in response.text
    assert "ACTUAL_COMMITTED_LATE_LOG" in response.text
    if not direct:
        plan_event = "plan_completed" if success else "plan_failed"
        task_event = "task_completed" if success else "task_failed"
        assert f'"event_type": "{plan_event}"' in response.text
        assert response.text.count(f'"event_type": "{task_event}"') == 1


def test_approval_is_required_before_execution(
    settings: Settings, repository: Repository, sandbox: FakeSandbox
) -> None:
    settings.require_approval = True
    with TestClient(create_app(settings, repository, sandbox)) as client:
        plan_id = client.post("/api/plan", json={"intent": "smoke"}).json()["plan_graph"]["id"]
        assert client.post(f"/api/plans/{plan_id}/execute").status_code == 409
        assert client.post(f"/api/plans/{plan_id}/approve").status_code == 200
        assert client.post(f"/api/plans/{plan_id}/execute").status_code == 202
