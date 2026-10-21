import pytest
from pydantic import ValidationError

from openresearch.models.ablation import AblationBudget, AblationCandidate, AblationCandidates
from openresearch.research.ablation import select_candidates


def candidate(identifier, category, minutes, **kwargs):
    return AblationCandidate(
        id=identifier,
        category=category,
        title=identifier,
        hypothesis="Compare actual behavior",
        change="Change one supported argument",
        arguments=["--variant", identifier],
        estimated_minutes=minutes,
        **kwargs,
    )


def test_selection_respects_total_budget_diversity_and_cpu_restriction():
    candidates = [
        candidate("module", "module", 4),
        candidate("duplicate", "module", 1),
        candidate("seed", "seed_stability", 2),
        candidate("gpu", "runtime_cost", 1, estimated_gpu_minutes=2),
        candidate("too_slow", "parameter", 20),
    ]
    plan = select_candidates(
        AblationCandidates(candidates=candidates),
        [],
        AblationBudget(max_experiments=3, max_wall_minutes=6, max_gpu_minutes=0),
    )
    assert {item.id for item in plan.selected} == {"duplicate", "seed"}
    assert set(plan.pruned_ids) == {"module", "gpu", "too_slow"}
    assert sum(item.estimated_minutes for item in plan.selected) <= 6
    assert all(item.estimated_gpu_minutes == 0 for item in plan.selected)


@pytest.mark.parametrize("parents", [("b", "a"), ("missing", None)])
def test_candidate_tree_rejects_cycles_and_unavailable_parents(parents):
    with pytest.raises(ValidationError):
        AblationCandidates(
            candidates=[
                candidate("a", "module", 1, parent_id=parents[0]),
                candidate("b", "parameter", 1, parent_id=parents[1]),
            ]
        )


def test_source_fallback_only_uses_options_that_exist_in_actual_code():
    from openresearch.research.ablation_fallback import fallback_candidates

    proposal = fallback_candidates(
        "import sys\nif 'ablated' in sys.argv:\n    value = 1\n",
        [],
        [],
    )
    assert [item.arguments for item in proposal.candidates] == [["ablated"]]
    assert fallback_candidates("print('no experiment flags')", [], []) is None


def test_plain_reproduction_does_not_add_tot_but_requested_budget_is_preserved():
    from openresearch.research.ablation import requested_ablation_budget

    assert requested_ablation_budget("复现 Transformer 核心架构并跑通测试") is None
    assert requested_ablation_budget("只做基线，不做消融") is None
    budget = requested_ablation_budget("最多2组轻量消融，墙钟6分钟，GPU 0分钟")
    assert budget.max_experiments == 2 and budget.max_wall_minutes == 6
    assert budget.max_gpu_minutes == 0


def test_ablation_task_is_required_before_acceptance_freeze_and_preparation(settings):
    from openresearch.models.context import Identity
    from openresearch.models.research import ResearchSpec
    from openresearch.models.submissions import PlanSubmission
    from openresearch.planner.research import build_research_plan

    plan = build_research_plan(
        PlanSubmission(
            "做最多2组轻量消融",
            Identity("alice", "tot"),
            code="print('source')",
            workflow="reproduce",
            research=ResearchSpec(),
        ),
        settings,
    )
    stages = {task.type: task for task in plan.nodes}
    search = stages["ablation_design"]
    assert search.assigned_to == "data_agent"
    assert search.inputs["research"]["ablation_search"]["max_experiments"] == 2
    assert search.id in stages["claim_freeze"].dependencies
    assert search.id in stages["research_prepare"].dependencies
    assert "ablation_research_spec" in stages["research_execute"].required_artifacts


def test_chat_does_not_downgrade_requested_ablation_on_provided_code(
    settings,
    repository,
    sandbox,
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from openresearch.api.app import create_app
    from openresearch.models.planning import IntentDraft
    from openresearch.services.intent import IntentParser

    async def classify(self, request, context):
        return IntentDraft(workflow="code")

    monkeypatch.setattr(IntentParser, "_classify", classify)
    message = "只规划：对以下代码做最多2组消融\n```python\nimport sys\nprint(sys.argv)\n```"
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post("/api/messages", json={"message": message})
    assert response.status_code == 200, response.text
    assert response.json()["type"] == "plan", response.text
    task = next(
        node for node in response.json()["plan_graph"]["nodes"] if node["type"] == "ablation_design"
    )
    assert task["inputs"]["code"] == "import sys\nprint(sys.argv)\n"
