import copy

import pytest
from fastapi.testclient import TestClient
from test_conversation_planning import HEADERS, choose

from openresearch.api.app import create_app
from openresearch.errors import ContractError
from openresearch.models.blueprint import PlanBlueprint
from openresearch.models.context import Identity
from openresearch.models.research import ResearchSpec
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.gap import GAP_PREFIX, requests_gap_repair
from openresearch.planner.materialize import materialize
from openresearch.services.plans import PlanService


@pytest.mark.parametrize(
    "text",
    [
        "复现论文，比较实际结果并修复差距后重跑",
        "修复结果不一致并重跑",
        "repair the result gap and rerun",
        "debug a result mismatch",
        "fix_and_rerun",
    ],
)
def test_human_gap_request_is_explicit(text):
    assert requests_gap_repair(text)


@pytest.mark.parametrize(
    "text",
    [
        "复现核心架构并跑通测试",
        "只修复依赖安装错误",
        "不要修复结果差距",
        "不重跑，只分析结果",
        "no fix for the result gap",
        "without result gap repair",
        "解释源码\n```python\nprint('修复结果差距并重跑')\n```",
        "解释 `fix_and_rerun` 的含义",
    ],
)
def test_code_literals_and_negative_or_unrelated_requests_do_not_authorize_gap_stage(text):
    assert not requests_gap_repair(text)


def template(settings, repository):
    return PlanService(settings, repository).build(
        PlanSubmission(
            "修复结果差距并重跑",
            Identity("alice", "native"),
            code="assert sum([2,3]) == 5\n",
            workflow="reproduce",
            research=ResearchSpec(max_repairs=2),
            result_gap_repair=True,
        )
    )


def test_gap_nodes_keep_separate_receipts_and_final_report_barrier(settings, repository):
    plan = template(settings, repository)
    source = next(node for node in plan.nodes if node.type == "research_execute")
    comparison = next(node for node in plan.nodes if node.type == "result_audit")
    gap = next(node for node in plan.nodes if node.type == "research_gap_repair")
    final = plan.nodes[-1]
    assert comparison.inputs["source_task_id"] == gap.inputs["source_task_id"] == source.id
    assert comparison.dependencies == [source.id] and gap.dependencies == [source.id, comparison.id]
    assert all(key.startswith(GAP_PREFIX) for key in gap.output_artifacts)
    assert not set(gap.output_artifacts) & set(source.output_artifacts)
    assert final.type == "chat_answer" and final.trigger_rule == "all_done"
    assert set(final.dependencies) == {node.id for node in plan.nodes[:-1]}
    assert final.inputs["gap_task_id"] == gap.id and not final.required_artifacts


def test_explicit_native_api_flag_is_scientific_and_does_not_implicitly_queue(
    settings, repository, sandbox
):
    with TestClient(create_app(settings, repository, sandbox)) as client:
        payload = {
            "intent": "修复结果差距并重跑",
            "code": "assert sum([2,3]) == 5\n",
            "workflow": "reproduce",
            "research": {"max_repairs": 2},
            "result_gap_repair": True,
        }
        response = client.post("/api/plan", json=payload, headers=HEADERS)
        assert response.status_code == 200, response.text
        plan = repository.get_plan(response.json()["plan_graph"]["id"])
        assert not plan.usage.started_at and any(
            node.type == "research_gap_repair" for node in plan.nodes
        )
        for workflow in ["code", "paper", "data", "framework"]:
            for research in [None, {"max_repairs": 2}]:
                invalid = {**payload, "workflow": workflow, "research": research}
                assert client.post("/api/plan", json=invalid, headers=HEADERS).status_code == 422
        invalid = {
            **payload,
            "comparison": {
                "targets": [{"name": "LangChain"}, {"name": "LlamaIndex"}],
                "mode": "research",
            },
        }
        rejected = client.post("/api/plan", json=invalid, headers=HEADERS)
        assert rejected.status_code == 422
        assert "scientific execution workflow" in rejected.json()["error"]
        assert not sandbox.created


def test_model_refinement_cannot_omit_rebind_or_move_gap_before_its_source(settings, repository):
    plan = template(settings, repository)
    refs = {node.id: f"node_{i}" for i, node in enumerate(plan.nodes)}
    data = {
        "strategy": "Retain all source identities",
        "nodes": [
            {
                "ref": refs[node.id],
                "template_task_id": node.id,
                "type": node.type,
                "name": node.name,
                "description": "Preserve stage",
                "assigned_to": node.assigned_to,
                "dependencies": [refs[parent] for parent in node.dependencies],
            }
            for node in plan.nodes
        ],
    }
    actual = materialize(PlanBlueprint.model_validate(data), plan, settings)
    assert [node.inputs for node in actual.nodes] == [node.inputs for node in plan.nodes]
    for fault in ["omit", "barrier", "source"]:
        bad = copy.deepcopy(data)
        gap = next(node for node in bad["nodes"] if node["type"] == "research_gap_repair")
        if fault == "omit":
            bad["nodes"].remove(gap)
        elif fault == "barrier":
            bad["nodes"][-1]["dependencies"] = []
        else:
            gap["dependencies"] = []
        with pytest.raises((ContractError, ValueError)):
            materialize(PlanBlueprint.model_validate(bad), plan, settings)


@pytest.mark.parametrize(
    "instructions,started", [("修复结果差距并重跑", True), ("先规划如何修复结果差距并重跑", False)]
)
def test_chat_builds_and_optionally_queues_native_gap_without_another_api_call(
    settings, repository, sandbox, monkeypatch, instructions, started
):
    choose(monkeypatch, workflow="reproduce")
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/messages",
            headers=HEADERS,
            json={
                "message": instructions + "\n```python\nassert sum([2,3]) == 5\n```",
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["type"] == "plan"
        plan = repository.get_plan(payload["plan_graph"]["id"])
        assert any(node.type == "research_gap_repair" for node in plan.nodes)
        assert (plan.usage.started_at is not None) == started
        assert ("已开始执行" in payload["response"]) == started
        assert not sandbox.created
