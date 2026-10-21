import json
from unittest.mock import AsyncMock

import pytest

from openresearch.agents.ablation import AblationDesigner, RejectedAblationProposals
from openresearch.errors import ModelError
from openresearch.models.ablation import AblationCandidates
from openresearch.models.context import Identity
from openresearch.models.research import ResearchSpec
from openresearch.models.submissions import PlanSubmission
from openresearch.research.ablation import adapter_argument_error
from openresearch.services.plans import PlanService


def proposal(arguments):
    return AblationCandidates.model_validate(
        {
            "candidates": [
                {
                    "id": "disable_feature",
                    "category": "module",
                    "title": "Disable actual feature",
                    "hypothesis": "Check actual source branch",
                    "change": "Use supported CLI switch",
                    "arguments": arguments,
                    "estimated_minutes": 1,
                }
            ]
        }
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["runner.py", "--disable"],
        ["./runner.py", "--disable"],
        ["python3.12", "runner.py"],
    ],
)
def test_argv_rejects_duplicate_launcher_components(arguments):
    assert "launcher" in adapter_argument_error(proposal(arguments), "runner.py")


def test_argv_does_not_treat_an_application_input_file_as_the_entrypoint():
    assert not adapter_argument_error(proposal(["data.py", "--disable"]), "runner.py")


async def test_rejected_model_argv_is_preserved_and_corrected_before_scoring(settings, files):
    agent = AblationDesigner(settings, files)
    invalid, corrected = proposal(["runner.py", "--disable"]), proposal(["--disable"])
    agent.llm.request_typed = AsyncMock(side_effect=[invalid, corrected])
    candidates, source, rejected = await agent._propose(
        "actual source", None, "source_fallback", "runner.py"
    )
    assert candidates == corrected and source == "model" and len(rejected) == 1
    assert rejected[0].candidates == invalid and agent.llm.request_typed.await_count == 2
    assert "launcher" in agent.llm.request_typed.call_args.args[0][-1]["content"]


@pytest.mark.parametrize("fallback", [False, True])
async def test_invalid_proposals_are_bounded_and_do_not_silently_strip_arguments(
    settings, files, fallback
):
    agent = AblationDesigner(settings, files)
    invalid = proposal(["runner.py", "--disable"])
    actual_fallback = proposal(["--disable"])
    agent.llm.request_typed = AsyncMock(return_value=invalid)
    if fallback:
        candidates, source, rejected = await agent._propose(
            "source", actual_fallback, "source_fallback", "runner.py"
        )
        assert candidates == actual_fallback and source == "source_fallback"
        assert len(rejected) == 2 and all(item.candidates == invalid for item in rejected)
    else:
        with pytest.raises(RejectedAblationProposals) as failure:
            await agent._propose("source", None, "source_fallback", "runner.py")
        assert len(failure.value.rejections) == 2
    assert (
        agent.llm.request_typed.await_count == 2
        and invalid.candidates[0].arguments[0] == "runner.py"
    )


async def test_failed_candidate_validation_keeps_audit_artifact_and_never_claims_execution(
    settings, repository, files
):
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "设计最多一组消融",
            Identity("alice", "invalid-argv"),
            "print('no CLI variants')",
            workflow="reproduce",
            research=ResearchSpec(),
        )
    )
    task = next(node for node in plan.nodes if node.type == "ablation_design")
    agent = AblationDesigner(settings, files)
    agent.llm.request_typed = AsyncMock(return_value=proposal(["openresearch_run.py", "--disable"]))
    result = await agent.execute(plan, task)
    assert not result.success and "ablation_plan" not in result.outputs
    audit = json.loads(result.outputs["ablation_selection_report"])
    assert not audit["execution_requested"] and len(audit["proposal_rejections"]) == 2
    assert all(
        item["candidates"]["candidates"][0]["arguments"][0] == "openresearch_run.py"
        for item in audit["proposal_rejections"]
    )


async def test_unavailable_model_cannot_bypass_argv_validation_via_declared_design_fallback(
    settings, files
):
    agent = AblationDesigner(settings, files)
    agent.llm.request_typed = AsyncMock(side_effect=ModelError("controlled unavailable model"))
    invalid = proposal(["runner.py", "--disable"])
    with pytest.raises(RejectedAblationProposals):
        await agent._propose("source", invalid, "design_fallback", "runner.py")
