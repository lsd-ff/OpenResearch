"""本文件的来源与模型为显式离线夹具；不构成真实库或公开网络验收。"""

import hashlib
import json
from datetime import timedelta

import pytest
from packaging.requirements import Requirement
from test_legacy_execution import legacy_client as legacy_client
from test_legacy_execution import result

from openresearch.llm.client import LLMClient
from openresearch.models.comparison import FrameworkAssessment
from openresearch.models.planning import IntentDraft
from openresearch.models.runtime import Status, utcnow
from openresearch.models.sources import PackageDescription
from openresearch.sources.frameworks import FrameworkSources


def payload(kind="framework_research", inputs=None):
    role = "librarian_agent"
    if kind in {"framework_recommendation", "framework_report"}:
        role = "data_agent"
    elif "prepare" in kind:
        role = "coder_agent"
    elif "run_" in kind:
        role = "sandbox_agent"
    return {
        "task_type": kind,
        "task_description": "对比 LangChain 和 LlamaIndex 的资料与适用性，只使用实际来源",
        "assigned_to": role,
        "inputs": {} if inputs is None else inputs,
    }


def comparison(count=2):
    return {
        "mode": "research",
        "targets": [
            {"name": name, "requirements": [f"{name.lower()}-lib==1.0"]}
            for name in ["Alpha", "Beta", "Gamma", "Delta"][:count]
        ],
    }


@pytest.fixture
def actual_source_fixture(monkeypatch):
    calls = []

    async def describe(self, target, environment=None):
        calls.append(target.name)
        packages = []
        for value in target.requirements:
            name = Requirement(value).name
            summary = "Controlled source description for " + name
            packages.append(
                PackageDescription(
                    name=name,
                    version="1.0",
                    summary=summary,
                    metadata_url=f"https://pypi.org/pypi/{name}/1.0/json",
                    metadata_sha256=hashlib.sha256(summary.encode()).hexdigest(),
                )
            )
        return packages

    async def assess(self, messages, model, **kwargs):
        assert model is FrameworkAssessment
        supplied = json.loads(messages[-1]["content"])
        assert supplied["sources"][0]["quote"].startswith("Controlled source description")
        return FrameworkAssessment(response="仅依据提供的来源进行适用性分析。", source_ids=[0])

    monkeypatch.setattr(FrameworkSources, "describe", describe)
    monkeypatch.setattr(LLMClient, "request_typed", assess)
    return calls


async def test_research_fit_and_recommendation_share_frozen_sources_without_sandboxes(
    legacy_client,
    repository,
    sandbox,
    actual_source_fixture,
):
    source = result(
        await legacy_client.post(
            "/api/execute",
            json=payload(
                inputs={"comparison": comparison()},
            ),
        )
    )
    root = repository.get_plan(source["plan_id"])
    assert root.status == Status.PENDING and len(root.nodes) == 5
    assert root.usage.task_attempts == 4 and not sandbox.created
    catalog = json.loads(source["outputs"]["framework_research_report"])
    assert len(catalog["studies"]) == 2 and "不表示已安装" in catalog["scope"]
    original_catalog = root.artifacts["framework_catalog"]
    started = root.usage.started_at
    fit = result(
        await legacy_client.post(
            "/api/execute",
            json=payload(
                "framework_fit_right",
                {"workflow_ref": source["workflow_ref"]},
            ),
        )
    )
    assert json.loads(fit["outputs"]["right_fit_report"])["name"] == "Beta"
    assert fit["outputs"]["right_fit_report"] == fit["outputs"]["framework_fit_report_2"]
    assert repository.get_plan(root.id).usage.task_attempts == 4
    final = result(
        await legacy_client.post(
            "/api/execute",
            json=payload(
                "framework_recommendation",
                {"workflow_ref": fit["workflow_ref"]},
            ),
        )
    )
    done = repository.get_plan(root.id)
    assert done.status == Status.COMPLETED and done.usage.started_at == started
    assert done.artifacts["framework_catalog"] == original_catalog
    assert all(task.run_count == 1 for task in done.nodes)
    assert sorted(actual_source_fixture) == ["Alpha", "Beta"] and not sandbox.created
    assert "alpha-lib==1.0" in final["outputs"]["evaluation_report"]
    assert json.loads(final["outputs"]["comparison_metrics"])["complete"]


async def test_numbered_fit_only_runs_selected_branch_then_catalog_continues_same_graph(
    legacy_client,
    repository,
    actual_source_fixture,
    sandbox,
):
    fit = result(
        await legacy_client.post(
            "/api/execute",
            json=payload(
                "framework_fit_3",
                {"comparison": comparison(3)},
            ),
        )
    )
    root = repository.get_plan(fit["plan_id"])
    assert actual_source_fixture == ["Gamma"] and root.usage.task_attempts == 2
    assert json.loads(fit["outputs"]["framework_fit_report_3"])["name"] == "Gamma"
    source = result(
        await legacy_client.post(
            "/api/execute",
            json=payload(
                inputs={"workflow_ref": fit["workflow_ref"]},
            ),
        )
    )
    assert source["plan_id"] == root.id and sorted(actual_source_fixture) == [
        "Alpha",
        "Beta",
        "Gamma",
    ]
    assert repository.get_plan(root.id).usage.task_attempts == 5 and not sandbox.created


async def test_approval_resumes_original_selected_branch_and_ignores_new_client_contract(
    legacy_client,
    repository,
    settings,
    actual_source_fixture,
):
    settings.require_approval = True
    pending = await legacy_client.post(
        "/api/execute",
        json=payload(
            "framework_fit_left",
            {"comparison": comparison()},
        ),
    )
    assert pending.status_code == 202 and not actual_source_fixture
    selected = pending.json()
    await legacy_client.post(f"/api/plans/{selected['plan_id']}/approve")
    actual = result(
        await legacy_client.post(
            "/api/execute",
            json={
                "plan_id": selected["plan_id"],
                "task_id": selected["task_id"],
                "inputs": {"target": {"name": "INJECTED"}},
            },
        )
    )
    assert actual_source_fixture == ["Alpha"]
    assert repository.get_plan(actual["plan_id"]).usage.task_attempts == 2


@pytest.mark.parametrize("mutation", ["foreign", "override", "branch", "absent", "mode", "role"])
async def test_continuation_rejects_foreign_or_incompatible_framework_selection(
    legacy_client,
    repository,
    actual_source_fixture,
    mutation,
):
    source = result(
        await legacy_client.post(
            "/api/execute",
            json=payload(
                inputs={"comparison": comparison()},
            ),
        )
    )
    body = payload("framework_fit_1", {"workflow_ref": source["workflow_ref"]})
    headers = {}
    if mutation == "foreign":
        headers["X-User-Id"] = "bob"
    elif mutation == "override":
        body["inputs"]["framework_research_report"] = "forged"
    elif mutation == "branch":
        root = repository.get_plan(source["plan_id"])
        body["inputs"]["stage_task_id"] = next(
            task.id for task in root.nodes if task.artifact_scope == "framework_2"
        )
    elif mutation == "absent":
        body["task_type"] = "framework_fit_4"
    elif mutation == "mode":
        body = payload("framework_run_1", {"workflow_ref": source["workflow_ref"]})
    else:
        body["assigned_to"] = "sandbox_agent"
    response = await legacy_client.post("/api/execute", json=body, headers=headers)
    assert response.status_code == (404 if mutation == "foreign" else 422), response.text
    assert len(actual_source_fixture) == 2


async def test_framework_continuation_keeps_original_time_budget(
    legacy_client,
    repository,
    actual_source_fixture,
):
    source = result(
        await legacy_client.post(
            "/api/execute",
            json=payload(
                inputs={"comparison": comparison()},
            ),
        )
    )
    with repository.database.transaction() as connection:
        root = repository.database.load(connection, source["plan_id"])
        root.usage.started_at = utcnow() - timedelta(seconds=root.budget.max_duration_seconds + 1)
        started = root.usage.started_at
        repository.database.save(connection, root)
    failed = await legacy_client.post(
        "/api/execute",
        json=payload(
            "framework_recommendation",
            {"workflow_ref": source["workflow_ref"]},
        ),
    )
    assert "event: error" in failed.text
    done = repository.get_plan(source["plan_id"])
    assert done.status == Status.CANCELED and done.usage.started_at == started
    assert len(actual_source_fixture) == 2


async def test_names_from_human_description_need_no_manual_comparison_spec(
    legacy_client,
    actual_source_fixture,
):
    source = result(await legacy_client.post("/api/execute", json=payload()))
    catalog = json.loads(source["outputs"]["framework_catalog"])
    assert [target["name"] for target in catalog["comparison"]["targets"]] == [
        "LangChain",
        "LlamaIndex",
    ]
    assert sorted(actual_source_fixture) == ["LangChain", "LlamaIndex"]


async def test_model_cannot_add_framework_not_named_by_human(legacy_client, monkeypatch):
    async def propose(self, messages, model, **kwargs):
        return IntentDraft(workflow="framework", frameworks=["Alpha", "Invented"])

    monkeypatch.setattr(LLMClient, "request_typed", propose)
    body = payload()
    body["task_description"] = "比较 Alpha 与 Beta 的资料"
    response = await legacy_client.post("/api/execute", json=body)
    assert response.status_code == 422 and "human" in response.text


async def test_failed_source_can_produce_honest_final_report_without_retrying_it(
    legacy_client,
    repository,
    actual_source_fixture,
    monkeypatch,
    sandbox,
):
    async def assess(self, messages, model, **kwargs):
        supplied = json.loads(messages[-1]["content"])
        invalid = supplied["sources"][0]["package"] == "alpha-lib"
        return FrameworkAssessment(
            response="明确夹具中的来源编号校验。", source_ids=[999 if invalid else 0]
        )

    monkeypatch.setattr(LLMClient, "request_typed", assess)
    initial = await legacy_client.post(
        "/api/execute", json=payload(inputs={"comparison": comparison()})
    )
    assert "event: error" in initial.text
    identifier = initial.headers["x-openresearch-plan-id"]
    failed = repository.get_plan(identifier)
    assert failed.status == Status.FAILED
    reported = result(
        await legacy_client.post(
            "/api/execute",
            json=payload(
                "framework_report",
                {"workflow_ref": "openresearch:" + identifier},
            ),
        )
    )
    assert not json.loads(reported["outputs"]["comparison_metrics"])["complete"]
    done = repository.get_plan(identifier)
    assert done.status == Status.FAILED and done.usage.started_at == failed.usage.started_at
    assert all(task.run_count == 1 for task in done.nodes if task.type == "framework_research")
    assert len(actual_source_fixture) == 2 and not sandbox.created
    assert "unknown or repeated source" in reported["outputs"]["evaluation_report"]


async def test_catalog_rechecks_actual_source_file_before_freezing_shared_versions(
    legacy_client,
    repository,
    files,
    actual_source_fixture,
    sandbox,
):
    selected = result(
        await legacy_client.post(
            "/api/execute",
            json=payload(
                "framework_fit_1",
                {"comparison": comparison()},
            ),
        )
    )
    source = repository.get_plan(selected["plan_id"])
    artifact = source.artifacts["framework_1__framework_research_report"]
    (files.root / artifact.location).write_text('{"forged":true}', encoding="utf-8")
    response = await legacy_client.post(
        "/api/execute",
        json=payload(
            inputs={"workflow_ref": selected["workflow_ref"]},
        ),
    )
    assert "event: error" in response.text and "event: result" not in response.text
    done = repository.get_plan(source.id)
    assert done.status == Status.FAILED and "framework_catalog" not in done.artifacts
    assert len(actual_source_fixture) == 2 and not sandbox.created
