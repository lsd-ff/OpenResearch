from openresearch.errors import ConflictError, ContractError
from openresearch.models.audit import ReviewSnapshot
from openresearch.models.context import Identity
from openresearch.models.legacy import DirectExecutionRequest
from openresearch.models.runtime import Budget, LegacyTaskOrigin, Plan, Task, TaskContract
from openresearch.research.review_sources import (
    SUPPLEMENT_KEYS,
    TERMINAL,
    capture_review,
    load_review,
    pin,
)
from openresearch.services.legacy_inputs import text_input
from openresearch.services.legacy_plans import managed_legacy_plan
from openresearch.services.legacy_stages import workflow_plan_id
from openresearch.services.plans import PlanService
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


def review_snapshot(
    payload: DirectExecutionRequest,
    identity: Identity,
    repository: Repository,
    files: ArtifactStore,
) -> ReviewSnapshot:
    if payload.plan_id or set(payload.inputs) - {"workflow_ref", "stage_task_id"}:
        raise ContractError("review only accepts an owned workflow_ref and optional stage_task_id")
    reference = text_input(payload.inputs, "workflow_ref", limit=80)
    if not reference:
        raise ContractError("review requires an owned workflow_ref with actual saved results")
    source = repository.get_plan(workflow_plan_id(reference), identity.user_id)
    selected = text_input(payload.inputs, "stage_task_id", limit=32)
    parent = next((node for node in source.nodes if node.type == "result_audit"), None)
    if parent is None:
        return capture_review(source, selected)
    if parent.status not in TERMINAL or "audit_report" not in source.artifacts:
        raise ConflictError("the preceding review has no terminal audit receipt")
    snapshot = ReviewSnapshot.model_validate(parent.inputs["snapshot"])
    if selected and selected != snapshot.source_task_id:
        raise ContractError("review continuation cannot silently switch source branches")
    for key, artifact in source.artifacts.items():
        if key in SUPPLEMENT_KEYS:
            snapshot.supplements[key] = pin(source.id, artifact)
    load_review(snapshot, identity.user_id, repository, files)
    return snapshot


def build_review_plan(
    payload: DirectExecutionRequest,
    identity: Identity,
    plans: PlanService,
    files: ArtifactStore,
    kind: str,
) -> Plan:
    snapshot = review_snapshot(payload, identity, plans.repository, files)
    scientific = {"benchmark_validate", "paper_compare", "claim_evidence_build"}
    material = load_review(snapshot, identity.user_id, plans.repository, files)
    if kind in scientific and material.task.type != "research_execute":
        raise ContractError("this review requires a managed scientific execution stage")
    outputs = {
        "benchmark_validate": ["benchmark_metrics", "benchmark_validation_report"],
        "verify_result": ["verification_report"],
        "paper_compare": ["comparison_report", "claim_evidence_graph", "claim_verification_report"],
        "claim_evidence_build": ["claim_evidence_graph", "claim_verification_report"],
        "result_visualization": ["result_plot", "plot_spec"],
        "render_plot": ["result_plot", "plot_spec"],
    }[kind]
    outputs = ["audit_report", *outputs]
    tools = ["results.audit"] + (
        ["claims.assess"] if kind in {"paper_compare", "claim_evidence_build"} else []
    )
    task = Task(
        name=payload.task_name or kind,
        type="result_audit",
        assigned_to="data_agent",
        description=payload.task_description,
        inputs={"operation": kind, "snapshot": snapshot.model_dump()},
        timeout_seconds=plans.settings.task_timeout_seconds,
        output_artifacts=outputs,
        contract=TaskContract(allowed_tools=tools, output_artifacts=outputs),
    )
    plan = managed_legacy_plan(payload.task_description, identity, plans, [task])
    plan.intent_type = "Result_Review"
    # 审核是新的只读任务，有独立审批和一次尝试；绝不重置或扩容源科研计划的预算。
    plan.budget = Budget(
        max_task_attempts=1, max_duration_seconds=plans.settings.task_timeout_seconds
    )
    plan.legacy = LegacyTaskOrigin(
        task_id=payload.task_id,
        task_type=kind,
        requested_type=payload.task_type,
        task_name=payload.task_name,
        assigned_to=payload.assigned_to,
        native_task_id=task.id,
    )
    return plan
