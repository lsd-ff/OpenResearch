from openresearch.errors import ContractError
from openresearch.models.context import Identity
from openresearch.models.legacy import DirectExecutionRequest
from openresearch.models.runtime import LegacyTaskOrigin, Plan, Task, TaskContract, Usage
from openresearch.research.gap import GAP_OUTPUTS, source_context
from openresearch.research.review_results import verify_results
from openresearch.research.review_sources import load_review
from openresearch.research.specifications import resolved_research
from openresearch.services.legacy_plans import managed_legacy_plan
from openresearch.services.legacy_reviews import review_snapshot
from openresearch.services.plans import PlanService
from openresearch.storage.files import ArtifactStore


def build_gap_plan(
    payload: DirectExecutionRequest, identity: Identity, plans: PlanService, files: ArtifactStore
) -> Plan:
    snapshot = review_snapshot(payload, identity, plans.repository, files)
    material = load_review(snapshot, identity.user_id, plans.repository, files)
    verified = verify_results(material, plans.settings, plans.repository, files)
    if not verified.audit.integrity_valid or verified.research is None:
        raise ContractError("gap repair requires an intact actual scientific execution receipt")
    spec = resolved_research(material.plan, material.task, files)
    if "research.patch" not in material.task.contract.allowed_tools or spec.max_repairs == 0:
        raise ContractError("the original workflow does not authorize result-gap source repair")
    if spec.benchmark and "benchmark.preflight" not in material.task.contract.allowed_tools:
        raise ContractError("benchmark gap repair requires the original preflight permission")
    if verified.research.runs[-1].attempts[-1].phase == "resources":
        raise ContractError("source patches cannot bypass refused resource admission")
    context = source_context(material, files)
    outputs = [*context, "research_spec", "environment_lock", *GAP_OUTPUTS]
    if spec.benchmark:
        outputs += ["benchmark_preflight_report", "benchmark_preflight_patch"]
    tools = list(material.task.contract.allowed_tools)
    task = Task(
        name=payload.task_name or "修复结果差距并实际重跑",
        type="research_gap_repair",
        assigned_to="coder_agent",
        description=payload.task_description,
        inputs={"snapshot": snapshot.model_dump(), "research": spec.model_dump()},
        timeout_seconds=plans.settings.task_timeout_seconds,
        output_artifacts=outputs,
        contract=TaskContract(
            allowed_tools=list(dict.fromkeys([*tools, "results.audit", "results.gap_repair"])),
            output_artifacts=outputs,
            optional_output_artifacts=[
                key
                for key in outputs
                if key
                in {
                    "research_report",
                    "benchmark_predictions",
                    "final_patch",
                    "claim_evidence_graph",
                    "claim_verification_report",
                    "benchmark_preflight_report",
                    "benchmark_preflight_patch",
                }
            ],
        ),
    )
    plan = managed_legacy_plan(payload.task_description, identity, plans, [task])
    plan.intent_type = "Result_Gap_Repair"
    plan.attachments = material.plan.attachments
    original = plans.repository.get_plan(snapshot.source_plan_id, identity.user_id)
    # 独立记录沿用原累计预算与首次起算时间；不会为修复重新获得完整时长或尝试数。
    plan.budget = original.budget.model_copy()
    plan.usage = Usage(
        task_attempts=original.usage.task_attempts, started_at=original.usage.started_at
    )
    plan.legacy = LegacyTaskOrigin(
        task_id=payload.task_id,
        task_type="fix_and_rerun",
        requested_type=payload.task_type,
        task_name=payload.task_name,
        assigned_to=payload.assigned_to,
        native_task_id=task.id,
    )
    return plan
