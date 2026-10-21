import asyncio
import json
import os

import pytest

from openresearch.errors import ContractError
from openresearch.models.blueprint import PlanBlueprint
from openresearch.models.runtime import Edge, Plan, Status
from openresearch.planner.agent import PlannerAgent
from openresearch.planner.code import build_code_plan
from openresearch.planner.materialize import materialize
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker


def comparison_template(settings):
    nodes = []
    for index in (1, 2):
        scope = f"framework_{index}"
        branch = build_code_plan(
            f"branch {index}",
            "alice",
            "comparison",
            settings,
            f"print({index})",
        )
        for task in branch.nodes:
            task.artifact_scope = scope
            task.output_artifacts = [f"{scope}__{key}" for key in task.output_artifacts]
            task.required_artifacts = [f"{scope}__{key}" for key in task.required_artifacts]
            task.contract.input_artifacts = list(task.required_artifacts)
            task.contract.output_artifacts = list(task.output_artifacts)
        nodes.extend(branch.nodes)
    return Plan(
        user_intent="compare two actual branches",
        intent_type="Framework_Comparison",
        owner_id="alice",
        session_id="comparison",
        nodes=nodes,
        edges=[
            Edge(**{"from": parent, "to": task.id})
            for task in nodes
            for parent in task.dependencies
        ],
    )


def blueprint(template):
    refs = {task.id: f"stage_{index}" for index, task in enumerate(template.nodes)}
    return PlanBlueprint(
        strategy="independent branches",
        nodes=[
            {
                "ref": refs[task.id],
                "template_task_id": task.id,
                "type": task.type,
                "assigned_to": task.assigned_to,
                "name": task.name,
                "description": "retain actual branch",
                "dependencies": [refs[parent] for parent in task.dependencies],
            }
            for task in template.nodes
        ],
    )


def test_planner_retains_two_distinct_branches_with_the_same_stage_types(settings, repository):
    template = comparison_template(settings)
    context = json.loads(PlannerAgent(settings, repository)._context(template))
    assert len({stage["ref"] for stage in context["required_stages"]}) == 4
    result = materialize(blueprint(template), template, settings)
    assert [task.artifact_scope for task in result.nodes] == [
        "framework_1",
        "framework_1",
        "framework_2",
        "framework_2",
    ]
    assert [task.inputs for task in result.nodes if task.type == "code_generate"] == [
        {"code": "print(1)"},
        {"code": "print(2)"},
    ]


@pytest.mark.parametrize("fault", ["omit_branch", "reuse_node", "change_stage"])
def test_model_cannot_omit_or_reuse_a_branch_identity(settings, fault):
    template = comparison_template(settings)
    proposal = blueprint(template)
    if fault == "omit_branch":
        proposal.nodes = proposal.nodes[:2]
    elif fault == "reuse_node":
        proposal.nodes[2].template_task_id = proposal.nodes[0].template_task_id
    else:
        proposal.nodes[0].template_task_id = proposal.nodes[1].template_task_id
    with pytest.raises(ContractError):
        materialize(proposal, template, settings)


@pytest.mark.parametrize(
    "live",
    [
        False,
        pytest.param(
            True,
            marks=[
                pytest.mark.docker,
                pytest.mark.skipif(
                    os.environ.get("OPENRESEARCH_TEST_DOCKER") != "1", reason="Docker opt-in"
                ),
            ],
        ),
    ],
)
async def test_independent_branches_execute_and_publish_their_own_actual_outputs(
    settings,
    repository,
    files,
    sandbox,
    live,
):
    template = comparison_template(settings)
    repository.save_plan(template)
    worker = Worker(settings, repository, files, DockerSandbox(settings) if live else sandbox)
    try:
        repository.queue_plan(template.id, template.owner_id)
        async with asyncio.timeout(90):
            while repository.get_plan(template.id).status not in {Status.COMPLETED, Status.FAILED}:
                await worker.tick()
                await asyncio.sleep(0.02)
            await asyncio.gather(*(item.future for item in worker.active.values()))
        done = repository.get_plan(template.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        assert files.read(done.artifacts["framework_1__generated_code"]) == "print(1)"
        assert files.read(done.artifacts["framework_2__generated_code"]) == "print(2)"
        assert "generated_code" not in done.artifacts and "run_metrics" not in done.artifacts
        if live:
            left = json.loads(files.read(done.artifacts["framework_1__run_metrics"]))
            right = json.loads(files.read(done.artifacts["framework_2__run_metrics"]))
            assert left["stdout"] == "1\n" and right["stdout"] == "2\n"
            assert repository.cleanup_candidates("docker") == []
    finally:
        await worker._shutdown()


def test_branch_view_rejects_consuming_a_sibling_private_artifact(settings):
    from openresearch.agents.context import AgentContext
    from openresearch.agents.scopes import scoped_context
    from openresearch.models.execution import ExecutionLease

    template = comparison_template(settings)
    task = template.nodes[1].model_copy(
        update={
            "required_artifacts": ["framework_2__generated_code"],
        }
    )
    context = AgentContext(template, task, ExecutionLease(template.id, task.id, "test", "test"))
    with pytest.raises(ContractError, match="another branch"):
        scoped_context(context)
