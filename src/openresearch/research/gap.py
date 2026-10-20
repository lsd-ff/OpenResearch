import hashlib

from openresearch.errors import ContractError
from openresearch.models.gap import GapPatchIdentity
from openresearch.models.research import ResearchPatch, ResearchReport, ResearchSpec
from openresearch.models.runtime_profiles import TargetPython
from openresearch.research.code_guards import validate_code_repair
from openresearch.research.patches import apply_patch
from openresearch.research.review_sources import ReviewMaterial
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


def validate_gap_patch(
    current: dict[str, bytes],
    proposal: ResearchPatch,
    spec: ResearchSpec,
    python: TargetPython = "3.12",
) -> tuple[dict[str, bytes], list[GapPatchIdentity]]:
    updated = apply_patch(current, proposal, spec)
    identities = []
    for patch in proposal.files:
        if patch.path not in current:
            raise ContractError("result-gap repair must modify an existing authorized source")
        validate_code_repair(current[patch.path].decode("utf-8"), patch.content, python)
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
