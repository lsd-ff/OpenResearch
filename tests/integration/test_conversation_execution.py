import httpx
import pytest
from fastapi.testclient import TestClient

from openresearch.agents.claim_freeze import ClaimFreezer
from openresearch.api.app import create_app
from openresearch.errors import ContractError
from openresearch.models.claims import ClaimDraft, CriterionDraft, RubricDraft
from openresearch.models.context import Identity
from openresearch.models.execution import ExecutionLease
from openresearch.models.planning import ExperimentDesign, IntentDraft
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import new_id
from openresearch.models.sources import RepositorySpec
from openresearch.models.submissions import PlanSubmission
from openresearch.services.intent import IntentParser
from openresearch.services.plans import PlanService
from openresearch.sources.wheels import WheelSource


@pytest.mark.parametrize("plan_only,approval", [(False, False), (True, False), (False, True)])
def test_requested_test_run_is_queued_with_a_required_report(
    settings, repository, sandbox, monkeypatch, plan_only, approval
):
    async def parse(self, snapshot, message, attachments):
        return IntentDraft(workflow="reproduce")

    monkeypatch.setattr(IntentParser, "parse", parse)
    settings.require_approval = approval
    message = "用 https://github.com/pallets/itsdangerous 复现并跑通测试"
    if plan_only:
        message = "先规划，不要执行。" + message
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/messages",
            json={"message": message},
            headers={"X-User-Id": "alice", "X-Session-Id": "auto-run"},
        )
    assert response.status_code == 200, response.text
    graph = response.json()["plan_graph"]
    expected = "awaiting_approval" if approval else "pending" if plan_only else "ready"
    assert graph["status"] == expected
    summary = next(node for node in graph["nodes"] if node["type"] == "chat_answer")
    execute = next(node for node in graph["nodes"] if node["type"] == "research_execute")
    assert execute["id"] in summary["dependencies"]
    assert set(summary["dependencies"]) == {
        node["id"] for node in graph["nodes"] if node != summary
    }
    assert summary["trigger_rule"] == "all_done" and not summary["required_artifacts"]
    frozen = next(node for node in graph["nodes"] if node["type"] == "claim_freeze")
    design = next(node for node in graph["nodes"] if node["type"] == "research_design")
    assert design["id"] in frozen["dependencies"]
    assert repository.get_plan(graph["id"]).status.value == expected


async def test_design_acceptance_is_frozen_before_code_and_cannot_be_empty(
    settings, repository, files
):
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "verify the actual causal mask",
            Identity("alice", "criteria"),
            workflow="reproduce",
            research=ResearchSpec(
                repository=RepositorySpec(url="https://github.com/pallets/itsdangerous")
            ),
            auto_design=True,
        )
    )
    task = next(node for node in plan.nodes if node.type == "claim_freeze")
    lease = ExecutionLease(plan.id, task.id, new_id(), "test")
    design = ExperimentDesign(rationale="test the selected source")
    plan.artifacts["experiment_design"] = files.write(
        lease, "experiment_design", design.model_dump_json()
    )
    freezer = ClaimFreezer(settings, files)
    with pytest.raises(ContractError, match="acceptance checks"):
        await freezer.execute(plan, task)
    design.rubric = RubricDraft(
        claims=[
            ClaimDraft(
                title="causality",
                statement="future tokens do not affect the prefix",
                criteria=[
                    CriterionDraft(description="assert prefix invariance after future mutation")
                ],
            )
        ]
    )
    plan.artifacts["experiment_design"] = files.write(
        lease, "experiment_design", design.model_dump_json()
    )
    result = await freezer.execute(plan, task)
    assert "future tokens do not affect the prefix" in result.outputs["claim_rubric"]


async def test_oversized_dependency_is_rejected_before_cache_access_or_download(settings):
    settings.dependency_max_bytes = 1024
    source = WheelSource(settings)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("no download"))
    ) as client:
        with pytest.raises(ContractError, match="experiment budget"):
            await source._asset(client, {"size": 2048})
