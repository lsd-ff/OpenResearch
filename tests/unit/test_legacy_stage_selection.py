import pytest

from openresearch.errors import ContractError
from openresearch.models.comparison import ComparisonSpec
from openresearch.models.context import Identity
from openresearch.models.legacy import DirectExecutionRequest
from openresearch.models.research import ResearchSpec
from openresearch.models.sources import RepositorySpec
from openresearch.models.submissions import PlanSubmission
from openresearch.services.legacy_stages import select_stage, stage_spec
from openresearch.services.plans import PlanService


@pytest.mark.parametrize(
    "kind", ["claim_rubric_extract", "repo_prepare", "benchmark_adapter_generate"]
)
def test_scientific_stage_selector_requires_its_actual_source_contract(settings, repository, kind):
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "执行原始断言",
            Identity("alice", "selection"),
            "assert 1 + 2 == 3",
            workflow="reproduce",
            research=ResearchSpec(),
        )
    )
    with pytest.raises(ContractError, match="requires"):
        select_stage(plan, DirectExecutionRequest(), kind)


def test_repository_preparation_selects_hash_checked_adapter_after_actual_source_stage(
    settings, repository
):
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "复现已有仓库",
            Identity("alice", "selection"),
            workflow="reproduce",
            research=ResearchSpec(
                repository=RepositorySpec(url="https://github.com/example/actual")
            ),
        )
    )
    target = select_stage(plan, DirectExecutionRequest(), "repo_prepare")
    assert target.type == "research_prepare"
    assert any(
        task.type == "repository_fetch" and task.id in target.dependencies for task in plan.nodes
    )


def test_branch_selection_never_chooses_an_arbitrary_framework_branch(settings, repository):
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "比较两种框架",
            Identity("alice", "comparison"),
            workflow="framework",
            comparison=ComparisonSpec(
                targets=[{"name": "Left"}, {"name": "Right"}], answer_mode="none"
            ),
        )
    )
    with pytest.raises(ContractError, match="ambiguous"):
        select_stage(plan, DirectExecutionRequest(), "benchmark_adapter_preflight")
    right = next(
        task
        for task in plan.nodes
        if task.type == "benchmark_preflight" and task.artifact_scope == "framework_2"
    )
    selected = select_stage(
        plan,
        DirectExecutionRequest(inputs={"stage_task_id": right.id}),
        "benchmark_adapter_preflight",
    )
    assert selected.id == right.id and selected.artifact_scope == "framework_2"
    wrong = next(task for task in plan.nodes if task.type == "framework_design")
    with pytest.raises(ContractError, match="absent"):
        select_stage(
            plan,
            DirectExecutionRequest(inputs={"stage_task_id": wrong.id}),
            "benchmark_adapter_preflight",
        )


@pytest.mark.parametrize(
    "raw",
    [
        {"dependency_spec": ["pyyaml>=6"]},
        {"workspace_path": "C:/private"},
        {"runtime_session": "dk-forged"},
        {
            "research": {"environment": {"requirements": ["pyyaml==6.0.3"]}},
            "dependency_spec": ["pyyaml==6.0.2"],
        },
        {
            "research": {"repository": {"url": "https://github.com/example/first"}},
            "repo_url": "https://github.com/example/second",
        },
    ],
)
def test_stage_declarations_cannot_drop_versions_sources_or_import_unmanaged_artifacts(
    settings, repository, raw
):
    with pytest.raises(ContractError):
        stage_spec(DirectExecutionRequest(inputs=raw), PlanService(settings, repository))


def test_stage_pin_declaration_becomes_the_frozen_scientific_environment(settings, repository):
    spec = stage_spec(
        DirectExecutionRequest(inputs={"dependency_spec": '["PyYAML==6.0.3"]'}),
        PlanService(settings, repository),
    )
    assert spec.environment.requirements == ["pyyaml==6.0.3"]
