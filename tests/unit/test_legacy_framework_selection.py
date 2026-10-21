import pytest

from openresearch.errors import ContractError
from openresearch.models.comparison import ComparisonSpec, FrameworkResearchReport, FrameworkTarget
from openresearch.models.context import Identity
from openresearch.models.legacy import DirectExecutionRequest
from openresearch.models.sources import PackageDescription
from openresearch.research.framework_catalog import pinned_target
from openresearch.services.legacy_frameworks import (
    build_framework_stage,
    framework_stage,
    select_framework_stage,
)
from openresearch.services.legacy_plans import validate_legacy_request
from openresearch.services.plans import PlanService


@pytest.mark.parametrize(
    "kind,index,native",
    [
        ("framework_fit_left", 1, "framework_research"),
        ("framework_fit_right", 2, "framework_research"),
        ("framework_prepare_3", 3, "research_prepare"),
        ("framework_run_4", 4, "research_execute"),
    ],
)
def test_bounded_branch_names_resolve_to_existing_native_stages(kind, index, native):
    rule = framework_stage(kind)
    assert rule.index == index and rule.native_type == native
    validate_legacy_request(
        DirectExecutionRequest(
            task_type=kind,
            task_description="process the actual branch",
            assigned_to=next(iter(rule.roles)),
        )
    )


@pytest.mark.parametrize(
    "kind", ["framework_fit_0", "framework_run_5", "framework_fit_01", "framework_prepare_other"]
)
def test_unknown_or_unbounded_branch_names_are_rejected(kind):
    assert framework_stage(kind) is None
    with pytest.raises(ContractError):
        validate_legacy_request(DirectExecutionRequest(task_type=kind, task_description="unknown"))


def test_catalog_freezes_real_package_version_and_preserves_user_identity():
    target = FrameworkTarget(name="Alpha", requirements=["alpha-lib>=1,<2"], import_roots=["alpha"])
    report = FrameworkResearchReport(
        name="Alpha",
        target=target,
        packages=[
            PackageDescription(
                name="alpha-lib",
                version="1.2",
                metadata_url="https://pypi.org/pypi/alpha-lib/1.2/json",
                metadata_sha256="a" * 64,
            )
        ],
    )
    assert pinned_target(target, report).requirements == ["alpha-lib==1.2"]
    with pytest.raises(ContractError, match="constraints"):
        pinned_target(target.model_copy(update={"requirements": ["alpha-lib==1.1"]}), report)
    with pytest.raises(ContractError, match="identity"):
        pinned_target(
            target,
            report.model_copy(
                update={"packages": [report.packages[0].model_copy(update={"name": "other"})]}
            ),
        )
    assert "target" not in FrameworkResearchReport(name="old", packages=[]).model_dump()


def test_framework_stage_selects_same_branch_and_keeps_all_descendants(settings, repository):
    spec = ComparisonSpec(
        targets=[FrameworkTarget(name=name) for name in ["A", "B", "C"]], answer_mode="none"
    )
    payload = DirectExecutionRequest(task_type="framework_prepare_2", task_description="compare")
    plan, selected = build_framework_stage(
        payload,
        Identity("alice", "framework"),
        PlanService(settings, repository),
        payload.task_type,
        spec,
    )
    repository.save_plan(plan)  # 实际图校验：目录是所有设计阶段祖先，最终报告等待全部节点。
    target = next(task for task in plan.nodes if task.id == selected)
    assert target.artifact_scope == "framework_2" and target.type == "research_prepare"
    assert all(task.run_count == 0 for task in plan.nodes)
    assert len([task for task in plan.nodes if task.type == "research_execute"]) == 3
    other = next(
        task
        for task in plan.nodes
        if task.type == "research_prepare" and task.artifact_scope == "framework_1"
    )
    changed = payload.model_copy(update={"inputs": {"stage_task_id": other.id}})
    with pytest.raises(ContractError, match="branch"):
        select_framework_stage(plan, changed, "framework_prepare_2")
