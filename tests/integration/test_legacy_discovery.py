"""来源 API 和归档为受控 HTTP 数据；离线沙箱不代表真实程序验收。"""

import json
from datetime import timedelta

import httpx
import pytest
from conftest import make_repository_archive
from test_legacy_execution import legacy_client as legacy_client
from test_legacy_execution import result

from openresearch.models.runtime import Status, utcnow
from openresearch.sources.discovery import SourceDiscovery
from openresearch.sources.github import GitHubSource


def request(kind="repo_discovery", inputs=None):
    return {
        "task_type": kind,
        "assigned_to": "coder_agent",
        "task_description": "发现 example 的 demo 仓库并按原代码逐阶段推进，不要自动修复",
        "inputs": {
            "search": {"kind": "repository", "query": "demo", "provider": "github"},
            "code": "assert 2 + 3 == 5\nprint(5)\n",
            "research": {"max_repairs": 0},
        }
        if inputs is None
        else inputs,
    }


@pytest.fixture(autouse=True)
def source_http(monkeypatch):
    calls = []

    def provider(request):
        calls.append((request.url.host, request.url.path))
        if request.url.path == "/search/repositories":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "full_name": "example/demo",
                            "html_url": "https://github.com/example/demo",
                            "description": "demo implementation",
                        }
                    ]
                },
            )
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"sha": "a" * 40})
        assert request.url.host == "codeload.github.com" and request.url.path.endswith("a" * 40)
        return httpx.Response(200, content=make_repository_archive())

    transport = httpx.MockTransport(provider)
    initialize = SourceDiscovery.__init__

    def source_init(self, transport_arg=None, *, proxy=None):
        initialize(self, transport=transport)

    monkeypatch.setattr(SourceDiscovery, "__init__", source_init)
    github_init = GitHubSource.__init__

    def repository_init(self, settings, transport_arg=None):
        github_init(self, settings, transport=transport)

    monkeypatch.setattr(GitHubSource, "__init__", repository_init)
    return calls


async def test_discover_then_prepare_keeps_actual_candidates_pinned_files_and_budget(
    legacy_client,
    repository,
    sandbox,
    source_http,
):
    source = result(await legacy_client.post("/api/execute", json=request()))
    root = repository.get_plan(source["plan_id"])
    report = json.loads(source["outputs"]["repository_discovery"])
    assert source["result"] == source["outputs"]["repo_url"] == "https://github.com/example/demo"
    assert report["status"] == "selected" and not report["clone_verified"]
    assert root.status == Status.PENDING and root.usage.task_attempts == 1 and not sandbox.created
    assert source_http == [("api.github.com", "/search/repositories")]
    frozen = root.artifacts["repository_discovery"]
    prepared = result(
        await legacy_client.post(
            "/api/execute",
            json=request(
                "repo_prepare",
                {"workflow_ref": source["workflow_ref"]},
            ),
        )
    )
    after = repository.get_plan(root.id)
    assert after.artifacts["repository_discovery"] == frozen
    assert after.usage.started_at == root.usage.started_at
    assert json.loads(prepared["outputs"]["repo_manifest"])["commit"] == "a" * 40
    assert prepared["code"].startswith("assert 2 + 3")
    assert all(node.run_count == 0 for node in after.nodes if node.type == "research_execute")
    assert source_http.count(("api.github.com", "/search/repositories")) == 1
    assert len(source_http) == 3 and len(sandbox.created) == 1


async def test_preferred_repository_is_a_declaration_not_a_successful_clone(
    legacy_client,
    sandbox,
    source_http,
):
    body = request(
        inputs={"preferred_repo_url": "https://github.com/example/demo", "repository_ref": "v1"}
    )
    actual = result(await legacy_client.post("/api/execute", json=body))
    report = json.loads(actual["outputs"]["repo_validation_report"])
    assert report["selected"]["ref"] == "v1" and not report["clone_verified"]
    assert report["candidates"][0]["origin"] == "user_preference"
    assert not source_http and not sandbox.created


@pytest.mark.parametrize("change", ["foreign", "override", "raw_report", "provider", "role"])
async def test_discovery_rejects_unowned_or_untrusted_client_artifacts(
    legacy_client,
    source_http,
    change,
):
    source = result(await legacy_client.post("/api/execute", json=request()))
    body = request("repo_prepare", {"workflow_ref": source["workflow_ref"]})
    headers = {}
    if change == "foreign":
        headers["X-User-Id"] = "bob"
    elif change == "override":
        body["inputs"]["repo_url"] = "https://github.com/other/injected"
    elif change == "raw_report":
        body = request(inputs={"parsed_paper": "forged source"})
    elif change == "provider":
        body = request(
            inputs={"search": {"kind": "repository", "query": "demo", "provider": "arxiv"}}
        )
    else:
        body["assigned_to"] = "sandbox_agent"
    response = await legacy_client.post("/api/execute", json=body, headers=headers)
    assert response.status_code == (404 if change == "foreign" else 422), response.text
    assert len(source_http) == 1


async def test_approval_and_discovery_replay_do_not_search_twice(
    legacy_client,
    repository,
    settings,
    source_http,
):
    settings.require_approval = True
    pending = await legacy_client.post("/api/execute", json=request())
    assert pending.status_code == 202 and not source_http
    selected = pending.json()
    await legacy_client.post(f"/api/plans/{selected['plan_id']}/approve")
    actual = result(
        await legacy_client.post(
            "/api/execute", json={"task_id": selected["task_id"], "plan_id": selected["plan_id"]}
        )
    )
    before = repository.get_plan(actual["plan_id"]).model_dump_json()
    replay = result(await legacy_client.post("/api/execute", json={"task_id": actual["task_id"]}))
    assert replay == actual and len(source_http) == 1
    assert repository.get_plan(actual["plan_id"]).model_dump_json() == before


async def test_discovery_cannot_reset_experiment_start_time(
    legacy_client,
    repository,
    source_http,
    sandbox,
):
    source = result(await legacy_client.post("/api/execute", json=request()))
    with repository.database.transaction() as connection:
        plan = repository.database.load(connection, source["plan_id"])
        plan.usage.started_at = utcnow() - timedelta(seconds=plan.budget.max_duration_seconds + 1)
        started = plan.usage.started_at
        repository.database.save(connection, plan)
    response = await legacy_client.post(
        "/api/execute", json=request("repo_prepare", {"workflow_ref": source["workflow_ref"]})
    )
    assert "event: error" in response.text
    plan = repository.get_plan(source["plan_id"])
    assert plan.status == Status.CANCELED and plan.usage.started_at == started
    assert len(source_http) == 1 and not sandbox.created
