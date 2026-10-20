from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.comparison import ComparisonSpec
from openresearch.models.runtime import Approval, Budget, Edge, Plan, Status, Task
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.research import depend, research_task


def namespace_task(task: Task, scope: str) -> None:
    prefix = scope + "__"
    task.artifact_scope = scope
    task.required_artifacts = [
        prefix + key if key != "comparison_protocol" else key for key in task.required_artifacts
    ]
    task.output_artifacts = [prefix + key for key in task.output_artifacts]
    task.contract.input_artifacts = list(task.required_artifacts)
    task.contract.output_artifacts = list(task.output_artifacts)


def benchmark_branch(
    spec: ComparisonSpec, protocol: Task, index: int, settings: Settings
) -> list[Task]:
    target = spec.targets[index]
    design = research_task(
        "framework_design",
        ["framework.design"],
        [
            "research_spec",
            "experiment_design",
            "environment_lock",
            "framework_source",
        ],
        settings,
    )
    design.inputs["target"] = target.model_dump()
    design.inputs["comparison"] = spec.model_dump()
    depend(design, [protocol])
    freeze = research_task("claim_freeze", ["claims.freeze"], ["claim_rubric"], settings)
    freeze.assigned_to = "data_agent"
    depend(freeze, [design, protocol])
    prepare = research_task("research_prepare", ["research.patch"], ["research_patch"], settings)
    depend(prepare, [design, freeze, protocol])
    preflight = research_task(
        "benchmark_preflight",
        ["benchmark.preflight", "research.patch"],
        [
            "benchmark_preflight_report",
            "benchmark_preflight_patch",
        ],
        settings,
    )
    depend(preflight, [design, freeze, prepare, protocol])
    execute = research_task(
        "research_execute",
        ["research.run", "research.patch"],
        [
            "research_report",
            "benchmark_predictions",
            "final_patch",
            "claim_evidence_graph",
            "claim_verification_report",
        ],
        settings,
    )
    depend(execute, [design, freeze, prepare, preflight, protocol])
    nodes = [design, freeze, prepare, preflight, execute]
    if spec.needs_answers:
        answer = research_task(
            "framework_answer", ["comparison.answer"], ["framework_answer_report"], settings
        )
        answer.assigned_to = "chat_agent"
        depend(answer, [design, execute, protocol])
        nodes.append(answer)
    for task in nodes:
        task.name = target.name + " · " + task.name
        task.description = target.name + ": use the actual library and shared comparison protocol."
    # 先统一建立原始依赖，再一次性命名空间化，避免重复给已带前缀的输入加前缀。
    for task in nodes:
        namespace_task(task, f"framework_{index + 1}")
    return nodes


def build_framework_plan(submission: PlanSubmission, settings: Settings) -> Plan:
    spec = submission.comparison
    if spec is None:
        raise ContractError("framework comparison requires at least two named targets")
    protocol = research_task(
        "comparison_protocol", ["comparison.protocol"], ["comparison_protocol"], settings
    )
    protocol.assigned_to = "data_agent"
    protocol.name = "共同评测协议" if spec.mode == "benchmark" else "资料对比协议"
    protocol.inputs["comparison"] = spec.model_dump()
    nodes = [protocol]
    for index, target in enumerate(spec.targets):
        if spec.mode == "benchmark":
            nodes.extend(benchmark_branch(spec, protocol, index, settings))
        else:
            study = research_task(
                "framework_research",
                ["framework.research"],
                ["framework_research_report"],
                settings,
            )
            study.assigned_to = "librarian_agent"
            study.name = target.name + " · 来源与适用性"
            study.inputs["target"] = target.model_dump()
            depend(study, [protocol])
            namespace_task(study, f"framework_{index + 1}")
            nodes.append(study)
    report = research_task(
        "framework_report",
        ["comparison.report"],
        [
            "comparison_report",
            "comparison_metrics",
            "chat_answer",
        ],
        settings,
    )
    report.assigned_to = "data_agent"
    report.name = "实际框架对比报告"
    report.inputs["comparison"] = spec.model_dump()
    report.dependencies = [task.id for task in nodes]
    report.trigger_rule = "all_done"
    nodes.append(report)
    if len(nodes) > settings.max_task_attempts:
        raise ContractError(
            f"comparison requires {len(nodes)} stages; increase the configured attempt budget"
        )
    required = settings.require_approval
    return Plan(
        user_intent=submission.intent,
        intent_type="Framework_Comparison",
        owner_id=submission.identity.user_id,
        session_id=submission.identity.session_id,
        attachments=submission.attachments,
        nodes=nodes,
        edges=[
            Edge(**{"from": parent, "to": task.id})
            for task in nodes
            for parent in task.dependencies
        ],
        approval=Approval(required=required, status="pending" if required else "not_required"),
        status=Status.AWAITING_APPROVAL if required else Status.PENDING,
        budget=Budget(
            max_task_attempts=settings.max_task_attempts,
            max_duration_seconds=settings.plan_timeout_seconds,
        ),
    )
