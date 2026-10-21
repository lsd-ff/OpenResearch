import hashlib

from openresearch.errors import ContractError
from openresearch.models.audit import ReviewSnapshot
from openresearch.models.gap import GapPatchIdentity
from openresearch.models.graph import ancestor_map
from openresearch.models.research import ResearchPatch, ResearchReport, ResearchSpec
from openresearch.models.runtime import Plan, Task
from openresearch.models.runtime_profiles import TargetPython
from openresearch.research.code_guards import validate_code_repair
from openresearch.research.patches import apply_patch
from openresearch.research.review_sources import ReviewMaterial, pin
from openresearch.storage.files import ArtifactStore

GAP_OUTPUTS = [
    "gap_debug_report",
    "gap_patch_manifest",
    "rerun_report",
    "rerun_metrics",
    "research_report",
    "benchmark_predictions",
    "final_patch",
    "claim_evidence_graph",
    "claim_verification_report",
]
CONTEXT_KEYS = {
    "repository_snapshot",
    "parsed_paper",
    "source_index",
    "claim_rubric",
    "framework_source",
    "comparison_protocol",
    "shared_answers",
    "experiment_design",
    "ablation_plan",
    "selected_ablation_configs",
    "ablation_selection_report",
}


def source_context(material: ReviewMaterial, files: ArtifactStore) -> dict[str, str]:
    return {
        key: files.read(value)
        for key, value in material.plan.artifacts.items()
        if key in CONTEXT_KEYS
    }


def native_snapshot(plan: Plan, task: Task, files: ArtifactStore) -> ReviewSnapshot:
    if "gap_source_snapshot" not in task.required_artifacts:
        raise ContractError("native repair requires its declared comparison snapshot")
    snapshot = ReviewSnapshot.model_validate_json(files.read(plan.artifacts["gap_source_snapshot"]))
    if (
        snapshot.source_plan_id != plan.id
        or snapshot.source_task_id != task.inputs.get("source_task_id")
        or snapshot.source_task_id
        not in ancestor_map({node.id: node for node in plan.nodes})[task.id]
    ):
        raise ContractError("native repair snapshot differs from the frozen source ancestor")
    artifact = plan.artifacts.get("comparison_report")
    if artifact is None or artifact.producer_task_id not in task.dependencies:
        raise ContractError("native repair has no declared actual comparison")
    parent = next(node for node in plan.nodes if node.id == artifact.producer_task_id)
    if parent.inputs.get("source_task_id") != snapshot.source_task_id:
        raise ContractError("native comparison belongs to another execution source")
    snapshot.supplements["comparison_report"] = pin(plan.id, artifact)
    return snapshot


def validate_gap_patch(
    current: dict[str, bytes],
    proposal: ResearchPatch,
    spec: ResearchSpec,
    python: TargetPython = "3.12",
    *,
    preserve_raises: bool = True,
) -> tuple[dict[str, bytes], list[GapPatchIdentity]]:
    updated = apply_patch(current, proposal, spec)
    identities = []
    for patch in proposal.files:
        if patch.path not in current:
            raise ContractError("result-gap repair must modify an existing authorized source")
        validate_code_repair(
            current[patch.path].decode("utf-8"),
            patch.content,
            python,
            preserve_raises=preserve_raises,
        )
        identities.append(
            GapPatchIdentity(
                path=patch.path,
                before_sha256=hashlib.sha256(current[patch.path]).hexdigest(),
                after_sha256=hashlib.sha256(updated[patch.path]).hexdigest(),
            )
        )
    if not identities:
        raise ContractError("result-gap repair did not change any source")
    return updated, identities


def patch_identities(
    before: dict[str, bytes], after: dict[str, bytes], spec: ResearchSpec
) -> list[GapPatchIdentity]:
    return [
        GapPatchIdentity(
            path=path,
            before_sha256=hashlib.sha256(before[path]).hexdigest(),
            after_sha256=hashlib.sha256(after[path]).hexdigest(),
        )
        for path in spec.patch_paths
        if path in before and before[path] != after.get(path)
    ]


def report_context(report: ResearchReport) -> dict[str, object]:
    return {
        "success": report.success,
        "repository_commit": report.repository_commit,
        "dataset_sha256": report.dataset_sha256,
        "comparison": report.comparison,
        "runs": [
            {
                "variant": run.variant,
                "metrics": run.metrics,
                "attempts": [
                    {
                        "number": attempt.number,
                        "phase": attempt.phase,
                        "exit_code": attempt.exit_code,
                        "error": attempt.error[-2000:],
                        "stdout": attempt.stdout[-3000:],
                        "stderr": attempt.stderr[-5000:],
                    }
                    for attempt in run.attempts
                ],
            }
            for run in report.runs
        ],
    }
