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
        assert client.post("/api/plan", json={"intent": "smoke"}).status_code == 401
        assert (
            client.post(
                "/api/auth/session", headers={"Authorization": "Bearer test-token"}
            ).status_code
            == 200
        )
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


def test_approval_is_required_before_execution(
    settings: Settings, repository: Repository, sandbox: FakeSandbox
) -> None:
    settings.require_approval = True
    with TestClient(create_app(settings, repository, sandbox)) as client:
        plan_id = client.post("/api/plan", json={"intent": "smoke"}).json()["plan_graph"]["id"]
        assert client.post(f"/api/plans/{plan_id}/execute").status_code == 409
        assert client.post(f"/api/plans/{plan_id}/approve").status_code == 200
        assert client.post(f"/api/plans/{plan_id}/execute").status_code == 202
