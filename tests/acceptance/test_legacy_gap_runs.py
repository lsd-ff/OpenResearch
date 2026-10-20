import asyncio
import json

import pytest
from test_docker_smoke import pytestmark as pytestmark
from test_legacy_api import completed_result
from test_legacy_api import legacy_docker as legacy_docker
from test_legacy_stage_runs import fresh_benchmark, request
from test_research import PREDICTOR

from openresearch.llm.client import LLMClient
from openresearch.models.gap import GapProposal
from openresearch.models.research import FilePatch, ResearchPatch
from openresearch.models.runtime import Status

BAD = PREDICTOR.replace("== '1'", "== '0'")


@pytest.fixture
def model(monkeypatch):
    state = {"status": "patched", "calls": 0, "code": PREDICTOR}

    async def typed(self, messages, response_model):
        state["calls"] += 1
        supplied = json.loads(messages[-1]["content"])
        if response_model is ResearchPatch:
            return ResearchPatch(
                files=[
                    FilePatch(
                        path="openresearch_run.py",
                        base_sha256=supplied["allowed_paths"]["openresearch_run.py"]["base_sha256"],
                        content=state["runtime_code"],
                    )
                ]
            )
        assert response_model is GapProposal
        if not state.get("smoke"):
            assert supplied["actual_report"]["runs"][0]["metrics"]["accuracy"] == 0
        assert '"targets"' not in messages[-1]["content"]
        if state["status"] != "patched":
            return GapProposal(status=state["status"], diagnosis="受控提议：证据不足，不改动源码。")
        return GapProposal(
            status="patched",
            diagnosis="受控提议修复特征条件；程序与指标实际执行。",
            patch=ResearchPatch(
                files=[
                    FilePatch(
                        path="openresearch_run.py",
                        base_sha256=supplied["allowed_paths"]["openresearch_run.py"]["base_sha256"],
                        content=state["code"],
                    ),
                ]
            ),
        )

    monkeypatch.setattr(LLMClient, "request_typed", typed)
    return state


async def initial(client):
    inputs = await fresh_benchmark(client, BAD)
    inputs["research"]["max_repairs"] = 2
    uploaded = await client.post(
        "/api/uploads",
        files={
            "file": (
                "mixed.csv",
                (
                    "id,x,y\n"
                    + "".join(f"{i},{i % 2},{'yes' if i % 2 else 'no'}\n" for i in range(12))
                ).encode(),
                "text/csv",
            )
        },
    )
    inputs["research"]["benchmark"]["upload_id"] = uploaded.json()["id"]
    payload = request("benchmark_execute", inputs)
    payload["task_description"] = "运行原分类程序并按固定规则测量结果，允许有界源码修复"
    return completed_result(await client.post("/api/execute", json=payload))


async def test_zero_exit_result_gap_is_patched_then_fresh_preflight_and_actual_rerun(
    legacy_docker, repository, model
):
    client, sandbox = legacy_docker
    async with asyncio.timeout(240):
        original = await initial(client)
        root = repository.get_plan(original["plan_id"])
        old_report = json.loads(original["outputs"]["research_report"])
        assert old_report["success"] and old_report["runs"][0]["metrics"]["accuracy"] == 0
        assert old_report["runs"][0]["attempts"][-1]["exit_code"] == 0
        frozen = root.model_dump_json()
        actual = completed_result(
            await client.post(
                "/api/execute",
                json=request("fix_and_rerun", {"workflow_ref": original["workflow_ref"]}),
            )
        )
        debug = json.loads(actual["outputs"]["gap_debug_report"])
        report = json.loads(actual["outputs"]["research_report"])
        assert debug["before_metrics"] == {"baseline": {"accuracy": 0.0}}
        assert debug["after_metrics"] == {"baseline": {"accuracy": 1.0}}
        assert report["success"] and len(report["preflight"]["sample_ids"]) == 8
        assert len(json.loads(actual["outputs"]["benchmark_predictions"])["baseline"]) == 12
        assert (
            report["preflight"]["run"]["attempts"][0]["sandbox_id"]
            != report["runs"][0]["attempts"][0]["sandbox_id"]
        )
        derived = repository.get_plan(actual["plan_id"])
        assert derived.status == Status.COMPLETED and derived.nodes[0].run_count == 1
        assert derived.artifacts["claim_rubric"].sha256 == root.artifacts["claim_rubric"].sha256
        assert derived.usage.started_at == root.usage.started_at
        assert derived.usage.task_attempts == root.usage.task_attempts + 1
        assert model["calls"] == 1
        verified = completed_result(
            await client.post(
                "/api/execute",
                json={
                    "task_type": "verify_result",
                    "assigned_to": "data_agent",
                    "task_description": "独立重算",
                    "inputs": {"workflow_ref": actual["workflow_ref"]},
                },
            )
        )
        assert (
            json.loads(verified["outputs"]["audit_report"])["metrics"]["baseline"]["accuracy"] == 1
        )
        replay = completed_result(
            await client.post("/api/execute", json={"task_id": actual["task_id"]})
        )
        assert replay["outputs"] == actual["outputs"] and model["calls"] == 1
        assert repository.get_plan(root.id).model_dump_json() == frozen
    assert not await sandbox.inventory()


async def test_unsupported_gap_retains_zero_exit_metrics_and_runs_no_new_experiment(
    legacy_docker, repository, model, monkeypatch
):
    client, sandbox = legacy_docker
    async with asyncio.timeout(180):
        original = await initial(client)
        model["status"] = "insufficient_evidence"
        created = []
        create = sandbox.create

        async def record(plan_id, execution_id):
            created.append(await create(plan_id, execution_id))
            return created[-1]

        monkeypatch.setattr(sandbox, "create", record)
        actual = completed_result(
            await client.post(
                "/api/execute",
                json=request("fix_and_rerun", {"workflow_ref": original["workflow_ref"]}),
            )
        )
        debug = json.loads(actual["outputs"]["gap_debug_report"])
        assert debug["status"] == "insufficient_evidence" and not debug["execution_performed"]
        assert not created and "research_report" not in actual["outputs"]
        assert repository.get_plan(original["plan_id"]).status == Status.COMPLETED


async def test_completed_program_can_still_fail_original_frozen_criterion(legacy_docker, model):
    client, sandbox = legacy_docker
    async with asyncio.timeout(180):
        original = await initial(client)
        model["code"] = BAD.replace("real predictions written", "measured inverted predictions")
        actual = completed_result(
            await client.post(
                "/api/execute",
                json=request("fix_and_rerun", {"workflow_ref": original["workflow_ref"]}),
            )
        )
        debug = json.loads(actual["outputs"]["gap_debug_report"])
        assert (
            debug["status"] == "rerun_completed"
            and debug["after_metrics"]["baseline"]["accuracy"] == 0
        )
        assert debug["acceptance_summary"]["contradicted"] == 1
        assert "程序完成不表示结果差距已修复" in actual["result"]
    assert not await sandbox.inventory()


async def test_initial_gap_patch_and_preflight_share_the_two_repair_budget(
    legacy_docker, repository, files, model
):
    client, sandbox = legacy_docker
    async with asyncio.timeout(240):
        original = await initial(client)
        frozen = repository.get_plan(original["plan_id"]).model_dump_json()
        fail_full = "\nif len(rows) > 8:\n    predictions[99]\n"
        model["code"] = PREDICTOR.replace("json.loads", "json.load") + fail_full
        model["runtime_code"] = PREDICTOR + fail_full
        response = await client.post(
            "/api/execute",
            json=request("fix_and_rerun", {"workflow_ref": original["workflow_ref"]}),
        )
        assert "event: error" in response.text and "event: result" not in response.text
        actual = repository.get_plan(response.headers["x-openresearch-plan-id"])
        report = json.loads(files.read(actual.artifacts["research_report"]))
        assert actual.status == Status.FAILED and not report["success"]
        assert len(report["preflight"]["run"]["attempts"]) == 2
        assert len(report["runs"][0]["attempts"]) == 1 and model["calls"] == 2
        assert "IndexError" in report["runs"][0]["attempts"][0]["stderr"]
        assert repository.get_plan(original["plan_id"]).model_dump_json() == frozen
    assert not await sandbox.inventory()


async def test_paper_code_gap_without_benchmark_returns_actual_stdout(
    legacy_docker, repository, model
):
    client, sandbox = legacy_docker
    code = (
        "import math\nvalue = math.sqrt(4)\nassert math.sqrt(9) == 3\n"
        "print('ACTUAL_SMOKE_VALUE', value)\n"
    )
    model.update(smoke=True, code=code.replace("sqrt(4)", "sqrt(25)"))
    async with asyncio.timeout(120):
        original = completed_result(
            await client.post(
                "/api/execute",
                json={
                    "task_type": "paper_code_execute",
                    "assigned_to": "research_coding_agent",
                    "task_description": "运行功能样例并允许有界修复，不声称科学论文效果",
                    "inputs": {
                        "code": code,
                        "research": {
                            "max_repairs": 1,
                            "rubric": {
                                "claims": [
                                    {
                                        "title": "Numerical criterion unavailable",
                                        "statement": "accuracy >= 0.9",
                                        "criteria": [
                                            {
                                                "description": "accuracy needs an actual benchmark",
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
        frozen = repository.get_plan(original["plan_id"]).model_dump_json()
        actual = completed_result(
            await client.post(
                "/api/execute",
                json=request("fix_and_rerun", {"workflow_ref": original["workflow_ref"]}),
            )
        )
        assert actual["outputs"]["rerun_metrics"] == "ACTUAL_SMOKE_VALUE 5.0\n"
        assert "ACTUAL_SMOKE_VALUE 5.0" in actual["result"]
        report = json.loads(actual["outputs"]["research_report"])
        assert report["success"] and report["preflight"] is None
        assert report["runs"][0]["metrics"] == {} and model["calls"] == 1
        assert repository.get_plan(original["plan_id"]).model_dump_json() == frozen
    assert not await sandbox.inventory()
