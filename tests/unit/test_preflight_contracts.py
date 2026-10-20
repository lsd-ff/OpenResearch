from openresearch.models.context import Identity
from openresearch.models.research import BenchmarkSpec, ResearchSpec
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.research import build_research_plan
from openresearch.research.benchmark import Dataset


def test_benchmark_has_mandatory_preflight_after_patch_and_before_full_run(settings):
    plan = build_research_plan(
        PlanSubmission(
            "evaluate uploaded data",
            Identity("alice", "preflight"),
            code="print('real')",
            workflow="benchmark",
            research=ResearchSpec(
                benchmark=BenchmarkSpec(upload_id="dataset", input_columns=["x"], target_column="y")
            ),
        ),
        settings,
    )
    stages = {task.type: task for task in plan.nodes}
    preflight = stages["benchmark_preflight"]
    assert stages["research_prepare"].id in preflight.dependencies
    assert stages["claim_freeze"].id in preflight.dependencies
    assert preflight.id in stages["research_execute"].dependencies
    assert "benchmark_preflight_patch" in stages["research_execute"].required_artifacts


def test_preflight_sample_uses_first_eight_rows_and_withholds_targets():
    from openresearch.research.preflight import preflight_sample

    dataset = Dataset(
        inputs=[{"id": str(index), "inputs": {"x": index}} for index in range(12)],
        targets={str(index): "private-label" for index in range(12)},
        sha256="a" * 64,
    )
    sample = preflight_sample(dataset)
    assert [row["id"] for row in sample.inputs] == [str(index) for index in range(8)]
    assert list(sample.targets) == [str(index) for index in range(8)]
    assert all("private-label" not in str(row) for row in sample.inputs)
    assert len(dataset.inputs) == 12 and len(dataset.targets) == 12
    assert sample.sha256 == dataset.sha256
