import asyncio
import json
from collections import Counter

import pytest
from test_docker_smoke import pytestmark as pytestmark
from test_legacy_api import legacy_docker as legacy_docker
from test_research import PREDICTOR

from openresearch.errors import ModelError
from openresearch.llm.client import LLMClient
from openresearch.models.gap import GapProposal
from openresearch.models.planning import ExperimentDesign, IntentDraft
from openresearch.models.research import FilePatch, ResearchPatch
from openresearch.models.runtime import Status

BAD = PREDICTOR.replace("== '1'", "== '0'")


@pytest.fixture
def proposals(monkeypatch):
    state = {"status": "patched", "calls": Counter(), "code": PREDICTOR}

    async def typed(self, messages, model):
        state["calls"][model.__name__] += 1
        if model is IntentDraft:
            return IntentDraft(
                workflow="benchmark", input_columns=["x"], target_column="y", id_column="id"
            )
        if model is ExperimentDesign:
            return ExperimentDesign(
                rationale="明确夹具：保留给定分类器和固定准确率规则。",
                rubric={
                    "claims": [
                        {
                            "title": "Functional classifier",
                            "statement": "accuracy >= 0.9",
                            "criteria": [
                                {
                                    "description": "accuracy on declared functional rows",
                                    "metric_name": "accuracy",
                                    "expected_value": 0.9,
                                    "operator": "at_least",
                                }
                            ],
                        }
                    ]
                },
            )
        if model is ResearchPatch and state.get("baseline_patch"):
            context = json.loads(messages[-1]["content"])
            return ResearchPatch(
                files=[
                    FilePatch(
                        path="openresearch_run.py",
                        content=state["baseline_patch"],
                        base_sha256=context["allowed_paths"]["openresearch_run.py"]["base_sha256"],
                    )
                ]
            )
        if model is GapProposal:
            context = json.loads(messages[-1]["content"])
            assert context["actual_report"]["runs"][0]["metrics"]["accuracy"] == 0
            assert "comparison_report" in context and '"targets"' not in messages[-1]["content"]
            if state["status"] != "patched":
                return GapProposal(
                    status=state["status"], diagnosis="明确无补丁夹具，不能声称已重跑。"
                )
            return GapProposal(
                status="patched",
                diagnosis="明确补丁夹具；实际执行和指标由后端完成。",
                patch=ResearchPatch(
                    files=[
                        FilePatch(
                            path="openresearch_run.py",
                            content=state["code"],
                            base_sha256=context["allowed_paths"]["openresearch_run.py"][
                                "base_sha256"
                            ],
                        )
                    ]
                ),
            )
        raise ModelError("native workflow fixture does not implement this model contract")

    monkeypatch.setattr(LLMClient, "request_typed", typed)
    return state


async def submit(client, prefix="", code=BAD):
    raw = (
        "id,x,y\n" + "".join(f"{i},{i % 2},{'yes' if i % 2 else 'no'}\n" for i in range(12))
    ).encode()
    upload = await client.post("/api/uploads", files={"file": ("functional.csv", raw, "text/csv")})
    assert upload.status_code == 201
    response = await client.post(
        "/api/messages",
        json={
            "message": prefix + "评测给定分类器，输入列 x，标签列 y，ID 列 id；基线准确率至少 0.9。"
            "先测量原入口，然后比较结果并修复差距后重跑。保持数据、原断言和库。\n```python\n"
            + code
            + "```",
            "attachments": [upload.json()["id"]],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["type"] == "plan", response.text
    return response.json()


async def complete(repository, identifier):
    async with asyncio.timeout(240):
        while True:
            plan = repository.get_plan(identifier)
            if plan.nodes[-1].status == Status.COMPLETED:
                return plan
            await asyncio.sleep(0.05)


async def test_single_chat_runs_comparison_gap_repair_and_actual_final_report_in_one_plan(
    legacy_docker, repository, files, proposals
):
    client, sandbox = legacy_docker
    actual = await submit(client)
    assert "已开始执行" in actual["response"]
    plan = await complete(repository, actual["plan_graph"]["id"])
    assert plan.status == Status.COMPLETED and all(node.run_count == 1 for node in plan.nodes)
    assert plan.usage.task_attempts == len(plan.nodes)
    before = json.loads(files.read(plan.artifacts["research_report"]))
    after = json.loads(files.read(plan.artifacts["result_gap__research_report"]))
    debug = json.loads(files.read(plan.artifacts["result_gap__gap_debug_report"]))
    assert before["runs"][0]["metrics"]["accuracy"] == 0 and before["success"]
    assert after["runs"][0]["metrics"]["accuracy"] == 1 and after["success"]
    source = next(node for node in plan.nodes if node.type == "research_execute")
    gap = next(node for node in plan.nodes if node.type == "research_gap_repair")
    assert (
        debug["source"]["source_plan_id"] == plan.id
        and debug["source"]["source_task_id"] == source.id
    )
    assert (
        plan.artifacts["result_gap__claim_rubric"].sha256 == plan.artifacts["claim_rubric"].sha256
    )
    assert (
        debug["source"]["artifacts"]["research_report"]["artifact_id"]
        == plan.artifacts["research_report"].id
    )
    assert "修复后的实际运行" in plan.nodes[-1].result and "原冻结规则判定" in plan.nodes[-1].result
    assert proposals["calls"] == {"IntentDraft": 1, "ExperimentDesign": 1, "GapProposal": 1}
    response = await client.post(
        "/api/execute",
        json={
            "task_type": "verify_result",
            "assigned_to": "data_agent",
            "task_description": "独立重算新运行",
            "inputs": {"workflow_ref": "openresearch:" + plan.id, "stage_task_id": gap.id},
        },
    )
    assert "event: result" in response.text, response.text
    assert not await sandbox.inventory()


async def test_no_patch_does_not_shadow_original_receipt_as_a_new_execution(
    legacy_docker, repository, files, proposals
):
    client, sandbox = legacy_docker
    proposals["status"] = "insufficient_evidence"
    actual = await submit(client)
    plan = await complete(repository, actual["plan_graph"]["id"])
    assert plan.status == Status.COMPLETED and "result_gap__research_report" not in plan.artifacts
    assert "research_report" in plan.artifacts and "未重跑" in plan.nodes[-1].result
    gap = next(node for node in plan.nodes if node.type == "research_gap_repair")
    response = await client.post(
        "/api/execute",
        json={
            "task_type": "verify_result",
            "assigned_to": "data_agent",
            "task_description": "不能伪装新实验",
            "inputs": {"workflow_ref": "openresearch:" + plan.id, "stage_task_id": gap.id},
        },
    )
    assert "event: error" in response.text and "event: result" not in response.text
    audit = repository.get_plan(response.headers["x-openresearch-plan-id"])
    report = json.loads(files.read(audit.artifacts["audit_report"]))
    assert report["status"] == "unverifiable" and report["metrics"] == {}
    assert report["execution_success"] is None and not await sandbox.inventory()


async def test_rejected_patch_keeps_failed_plan_and_final_actual_failure_report(
    legacy_docker, repository, proposals
):
    client, sandbox = legacy_docker
    proposals["code"] = PREDICTOR.replace(
        "assert all('y' not in row['inputs'] for row in rows)\n", ""
    )
    actual = await submit(client)
    plan = await complete(repository, actual["plan_graph"]["id"])
    assert plan.status == Status.FAILED and plan.nodes[-1].status == Status.COMPLETED
    assert "补丁被拒绝" in plan.nodes[-1].result and "实际修复错误" in plan.nodes[-1].result
    assert (
        "result_gap__research_report" not in plan.artifacts
        and proposals["calls"]["GapProposal"] == 1
    )
    assert not await sandbox.inventory()


async def test_one_approval_covers_frozen_graph_without_resetting_first_start(
    legacy_docker, settings, repository, proposals
):
    client, sandbox = legacy_docker
    settings.require_approval = True
    actual = await submit(client)
    identifier = actual["plan_graph"]["id"]
    pending = repository.get_plan(identifier)
    assert pending.status == Status.AWAITING_APPROVAL and not pending.usage.started_at
    assert not await sandbox.inventory() and not proposals["calls"]["GapProposal"]
    assert (await client.post(f"/api/plans/{identifier}/approve")).status_code == 200
    assert (await client.post(f"/api/plans/{identifier}/execute")).status_code == 202
    started = repository.get_plan(identifier).usage.started_at
    final = await complete(repository, identifier)
    assert final.status == Status.COMPLETED and final.usage.started_at == started
    assert final.usage.task_attempts == len(final.nodes) and proposals["calls"]["GapProposal"] == 1
    assert final.budget == pending.budget and not await sandbox.inventory()


@pytest.mark.parametrize("formal", [False, True])
async def test_baseline_failure_blocks_repair_but_still_produces_actual_final_report(
    legacy_docker, repository, proposals, formal
):
    client, sandbox = legacy_docker
    code = BAD + "\nassert len(rows) <= 8\n" if formal else "assert sum([2,3]) == 99\n"
    actual = await submit(client, code=code)
    final = await complete(repository, actual["plan_graph"]["id"])
    source = next(node for node in final.nodes if node.type == "research_execute")
    gap = next(node for node in final.nodes if node.type == "research_gap_repair")
    assert source.status == (Status.FAILED if formal else Status.BLOCKED)
    assert gap.status == Status.BLOCKED
    assert final.status == Status.FAILED and not proposals["calls"]["GapProposal"]
    assert "修复节点未留下运行回执" in final.nodes[-1].result
    assert "不能从缺失产物认定已重跑或已修复" in final.nodes[-1].result
    assert "AssertionError" in final.nodes[-1].result
    assert not await sandbox.inventory()


async def test_initial_execution_cannot_delete_assertion_before_the_gap_stage(
    legacy_docker, repository, files, proposals
):
    client, sandbox = legacy_docker
    proposals["baseline_patch"] = BAD
    actual = await submit(client, code=BAD + "\nassert len(rows) <= 8\n")
    final = await complete(repository, actual["plan_graph"]["id"])
    assert final.status == Status.FAILED
    assert not proposals["calls"]["GapProposal"]
    original = json.loads(files.read(final.artifacts["final_patch"]))
    assert "assert len(rows) <= 8" in original["files"][0]["content"]
    assert not await sandbox.inventory()


async def test_legitimate_initial_api_repair_preserves_checks_and_earlier_failure_report(
    legacy_docker, repository, files, proposals
):
    client, sandbox = legacy_docker
    proposals["baseline_patch"] = BAD
    actual = await submit(client, code=BAD.replace("json.loads", "json.load"))
    final = await complete(repository, actual["plan_graph"]["id"])
    assert final.status == Status.COMPLETED and proposals["calls"]["ResearchPatch"] == 1
    before = json.loads(files.read(final.artifacts["research_report"]))
    assert len(before["preflight"]["run"]["attempts"]) == 2
    assert before["preflight"]["run"]["attempts"][0]["error"]
    assert "AttributeError" in final.nodes[-1].result and "预检第 1 次" in final.nodes[-1].result
    assert proposals["calls"]["GapProposal"] == 1 and not await sandbox.inventory()
