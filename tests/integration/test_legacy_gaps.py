import hashlib
import json
from datetime import timedelta

import pytest
from test_legacy_execution import legacy_client as legacy_client
from test_legacy_execution import result

from openresearch.llm.client import LLMClient
from openresearch.models.gap import GapProposal
from openresearch.models.research import FilePatch, ResearchPatch
from openresearch.models.runtime import Status, utcnow

CODE = "import math\nvalue = math.sqrt(4)\nassert math.sqrt(9) == 3\nprint(value)\n"


def gap_request(reference):
    return {
        "task_type": "fix_and_rerun",
        "assigned_to": "research_coding_agent",
        "task_description": "根据原结果诊断差距，保留来源、数据和断言，实际重跑并报告",
        "inputs": {"workflow_ref": reference},
    }


async def source(client, repairs=1):
    return result(
        await client.post(
            "/api/execute",
            json={
                "task_type": "paper_code_execute",
                "assigned_to": "research_coding_agent",
                "task_description": "运行原代码并保存回执",
                "inputs": {
                    "code": CODE,
                    "research": {
                        "max_repairs": repairs,
                        "rubric": {
                            "claims": [
                                {
                                    "title": "fixed criterion",
                                    "statement": "accuracy >= 0.9",
                                    "criteria": [
                                        {
                                            "description": "accuracy",
                                            "metric_name": "accuracy",
                                            "expected_value": 0.9,
                                            "operator": "at_least",
                                        }
                                    ],
                                }
                            ]
                        },
                    },
                },
            },
        )
    )


@pytest.fixture
def proposal(monkeypatch):
    state = {"code": None, "calls": 0}

    async def typed(self, messages, model):
        assert model is GapProposal
        state["calls"] += 1
        if callback := state.get("before_response"):
            callback()
        if state["code"] is None:
            return GapProposal(
                status="not_justified", diagnosis="离线明确夹具：没有足够来源支持更改。"
            )
        return GapProposal(
            status="patched",
            diagnosis="离线补丁夹具；不作为真实执行证据。",
            patch=ResearchPatch(
                files=[
                    FilePatch(
                        path="openresearch_run.py",
                        base_sha256=hashlib.sha256(CODE.encode()).hexdigest(),
                        content=state["code"],
                    ),
                ]
            ),
        )

    monkeypatch.setattr(LLMClient, "request_typed", typed)
    return state


async def test_no_justified_patch_preserves_source_budget_and_does_not_execute(
    legacy_client, repository, sandbox, proposal
):
    original = await source(legacy_client)
    before = repository.get_plan(original["plan_id"])
    frozen, containers = before.model_dump_json(), list(sandbox.created)
    actual = result(
        await legacy_client.post("/api/execute", json=gap_request(original["workflow_ref"]))
    )
    report = json.loads(actual["outputs"]["gap_debug_report"])
    assert report["status"] == "not_justified" and not report["execution_performed"]
    assert "research_report" not in actual["outputs"] and "没有执行" in actual["result"]
    assert actual["source_workflow_ref"] == original["workflow_ref"]
    plan = repository.get_plan(actual["plan_id"])
    assert plan.usage.started_at == before.usage.started_at and plan.budget == before.budget
    assert plan.usage.task_attempts == before.usage.task_attempts + 1
    replay = result(await legacy_client.post("/api/execute", json={"task_id": actual["task_id"]}))
    assert replay["outputs"] == actual["outputs"] and proposal["calls"] == 1
    assert (
        repository.get_plan(before.id).model_dump_json() == frozen and sandbox.created == containers
    )


async def test_valid_gap_patch_reuses_frozen_rules_and_can_be_independently_reviewed(
    legacy_client, repository, sandbox, proposal
):
    original = await source(legacy_client)
    before = repository.get_plan(original["plan_id"])
    proposal["code"] = CODE.replace("sqrt(4)", "sqrt(25)")
    count = len(sandbox.created)
    actual = result(
        await legacy_client.post("/api/execute", json=gap_request(original["workflow_ref"]))
    )
    report = json.loads(actual["outputs"]["gap_debug_report"])
    assert report["status"] == "rerun_completed" and report["execution_performed"]
    assert actual["outputs"]["rerun_metrics"] == "test-output"
    assert len(sandbox.created) == count + 1
    plan = repository.get_plan(actual["plan_id"])
    assert plan.artifacts["claim_rubric"].sha256 == before.artifacts["claim_rubric"].sha256
    reviewed = result(
        await legacy_client.post(
            "/api/execute",
            json={
                "task_type": "verify_result",
                "assigned_to": "data_agent",
                "task_description": "核对回执",
                "inputs": {"workflow_ref": actual["workflow_ref"]},
            },
        )
    )
    assert json.loads(reviewed["outputs"]["audit_report"])["status"] == "verified"
    assert repository.get_plan(before.id).model_dump_json() == before.model_dump_json()


@pytest.mark.parametrize(
    "change", ["foreign", "raw_report", "role", "missing_ref", "no_permission"]
)
async def test_gap_refuses_foreign_forged_or_unauthorized_source(legacy_client, proposal, change):
    original = await source(legacy_client, repairs=0 if change == "no_permission" else 1)
    payload, headers = gap_request(original["workflow_ref"]), {}
    if change == "foreign":
        headers["X-User-Id"] = "bob"
    elif change == "raw_report":
        payload["inputs"]["comparison_report"] = "all passed"
    elif change == "role":
        payload["assigned_to"] = "data_agent"
    elif change == "missing_ref":
        payload["inputs"] = {}
    response = await legacy_client.post("/api/execute", json=payload, headers=headers)
    assert response.status_code == (404 if change == "foreign" else 422), response.text
    assert proposal["calls"] == 0


@pytest.mark.parametrize(
    "candidate",
    [
        CODE.replace("assert math.sqrt(9) == 3\n", ""),
        CODE.replace("import math", "import cmath as math"),
    ],
)
async def test_gap_cannot_remove_existing_checks_or_used_libraries(
    legacy_client, repository, files, sandbox, proposal, candidate
):
    original = await source(legacy_client)
    proposal["code"] = candidate
    count = len(sandbox.created)
    response = await legacy_client.post("/api/execute", json=gap_request(original["workflow_ref"]))
    assert "event: error" in response.text
    failed = repository.get_plan(response.headers["x-openresearch-plan-id"])
    debug = json.loads(files.read(failed.artifacts["gap_debug_report"]))
    assert failed.status == Status.FAILED and debug["status"] == "rejected"
    assert debug["proposal"]["patch"]["files"][0]["content"] == candidate
    assert not debug["execution_performed"] and len(sandbox.created) == count


async def test_approval_checks_actual_source_bytes_again_before_model_or_execution(
    legacy_client, settings, repository, files, sandbox, proposal
):
    original = await source(legacy_client)
    settings.require_approval = True
    pending = await legacy_client.post("/api/execute", json=gap_request(original["workflow_ref"]))
    assert pending.status_code == 202
    plan = repository.get_plan(original["plan_id"])
    path = files.root / plan.artifacts["final_patch"].location
    path.write_text("changed", encoding="utf-8")
    selected, count = pending.json(), len(sandbox.created)
    await legacy_client.post(f"/api/plans/{selected['plan_id']}/approve")
    response = await legacy_client.post("/api/execute", json={"task_id": selected["task_id"]})
    assert "event: error" in response.text and not proposal["calls"]
    assert len(sandbox.created) == count


async def test_gap_cannot_restart_expired_original_duration(legacy_client, repository, proposal):
    original = await source(legacy_client)
    plan = repository.get_plan(original["plan_id"])
    plan.usage.started_at = utcnow() - timedelta(seconds=plan.budget.max_duration_seconds + 1)
    with repository.database.transaction() as connection:
        repository.database.save(connection, plan)
    response = await legacy_client.post("/api/execute", json=gap_request(original["workflow_ref"]))
    assert "event: error" in response.text and proposal["calls"] == 0


async def test_source_changed_during_model_wait_is_rejected_before_any_new_sandbox(
    legacy_client, repository, files, sandbox, proposal
):
    original = await source(legacy_client)
    plan = repository.get_plan(original["plan_id"])
    proposal["code"] = CODE.replace("sqrt(4)", "sqrt(25)")
    path = files.root / plan.artifacts["final_patch"].location
    proposal["before_response"] = lambda: path.write_text("changed", encoding="utf-8")
    count = len(sandbox.created)
    response = await legacy_client.post("/api/execute", json=gap_request(original["workflow_ref"]))
    assert "event: error" in response.text
    failed = repository.get_plan(response.headers["x-openresearch-plan-id"])
    debug = json.loads(files.read(failed.artifacts["gap_debug_report"]))
    assert not debug["execution_performed"] and not debug["preflight_performed"]
    assert proposal["calls"] == 1 and len(sandbox.created) == count
