import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openresearch.api.app import create_app
from openresearch.models.comparison import ComparisonSpec, FrameworkTarget
from openresearch.models.context import Identity
from openresearch.models.submissions import PlanSubmission
from openresearch.services.plans import PlanService


def test_comparison_builds_shared_protocol_and_isolated_full_research_branches(
    settings, repository
):
    comparison = ComparisonSpec(
        targets=[FrameworkTarget(name="LangChain"), FrameworkTarget(name="LlamaIndex")]
    )
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "对比 LangChain 与 LlamaIndex 的 RAG 性能",
            Identity("alice", "compare"),
            workflow="framework",
            comparison=comparison,
        )
    )
    assert plan.intent_type == "Framework_Comparison"
    protocol = next(task for task in plan.nodes if task.type == "comparison_protocol")
    report = next(task for task in plan.nodes if task.type == "framework_report")
    assert report.trigger_rule == "all_done" and not report.required_artifacts
    for scope in ("framework_1", "framework_2"):
        stages = [task for task in plan.nodes if task.artifact_scope == scope]
        assert {task.type for task in stages} >= {
            "framework_design",
            "claim_freeze",
            "research_prepare",
            "benchmark_preflight",
            "research_execute",
            "framework_answer",
        }
        assert all(protocol.id in task.dependencies for task in stages)
        assert all(key.startswith(scope + "__") for task in stages for key in task.output_artifacts)
    assert set(report.dependencies) == {task.id for task in plan.nodes if task.id != report.id}


def test_duplicate_frameworks_are_rejected_before_any_source_or_model_call():
    with pytest.raises(ValidationError):
        ComparisonSpec(
            targets=[FrameworkTarget(name="LangChain"), FrameworkTarget(name="langchain")]
        )


@pytest.mark.parametrize(
    ("only_plan", "approval", "expected"),
    [
        (False, False, "ready"),
        (True, False, "pending"),
        (False, True, "awaiting_approval"),
    ],
)
def test_chat_comparison_automatically_queues_without_bypassing_user_controls(
    settings, repository, sandbox, only_plan, approval, expected
):
    settings.require_approval = approval
    message = "对比 LangChain 与 LlamaIndex 的 RAG 性能"
    if only_plan:
        message += "，只规划，不要执行"
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/messages",
            json={"message": message},
            headers={
                "X-User-Id": "alice",
                "X-Session-Id": "comparison-chat",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["type"] == "plan"
        assert result["plan_graph"]["intent_type"] == "Framework_Comparison"
        assert result["plan_graph"]["status"] == expected
        assert len(result["plan_graph"]["nodes"]) == 14
