from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.compute import ReproductionRequest
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Approval, Budget, Edge, Plan, Status, Task, TaskContract
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.code import code_permissions
from openresearch.planner.compute import compute_task
from openresearch.planner.documents import document_task
from openresearch.research.ablation import requested_ablation_budget
from openresearch.research.compute import compute_runtime
from openresearch.research.reproduction_mode import request_from_intent
from openresearch.research.runtime_selection import bound_profile, runtime_tools


def research_task(kind: str, tools: list[str], outputs: list[str], settings: Settings) -> Task:
    if kind in {
        "environment_lock",
        "research_design",
        "framework_design",
        "benchmark_preflight",
        "research_execute",
    }:
        tools = [*tools, "runtime.version"]
    role = {"repository_fetch": "librarian_agent", "claim_freeze": "data_agent"}.get(
        kind, "coder_agent"
    )
    return Task(
        name=kind.replace("_", " ").title(),
        type=kind,
        assigned_to=role,
        timeout_seconds=settings.task_timeout_seconds,
        output_artifacts=outputs,
        contract=TaskContract(output_artifacts=outputs, allowed_tools=tools),
    )


def depend(task: Task, parents: list[Task]) -> None:
    task.dependencies = [parent.id for parent in parents]
    task.required_artifacts = [key for parent in parents for key in parent.output_artifacts]
    task.contract.input_artifacts = task.required_artifacts


def repair_tools(intent: str) -> list[str]:
    generation, execution = code_permissions(intent)
    return (["research.patch"] if "code.repair" in generation else []) + (
        ["environment.repair"] if "environment.lock" in execution else []
    )


def source_tasks(submission: PlanSubmission, spec: ResearchSpec, settings: Settings) -> list[Task]:
    nodes = [document_task(settings)] if submission.attachments else []
    if spec.repository is not None or submission.attachments:
        repository = research_task(
            "repository_fetch", ["repository.fetch"], ["repository_snapshot"], settings
        )
        if spec.repository:
            repository.inputs["repository"] = spec.repository.model_dump()
        elif nodes:
            depend(repository, nodes)
        repository.inputs["auto_discover"] = submission.auto_design
        nodes.append(repository)
    environment = research_task(
        "environment_lock",
        [
            "environment.lock",
            *runtime_tools(settings),
            *(
                ["environment.repair"]
                if "environment.repair" in repair_tools(submission.intent)
                else []
            ),
        ],
        ["environment_lock", "environment_preparation_report", "prepared_environment"],
        settings,
    )
    environment.inputs["environment"] = spec.environment.model_dump()
    environment.inputs["research"] = spec.model_dump()
    return [*nodes, environment]


def build_research_plan(submission: PlanSubmission, settings: Settings) -> Plan:
    spec = submission.research or ResearchSpec()
    request = spec.reproduction or request_from_intent(submission.intent)
    if spec.runtime and (profile := bound_profile(settings, spec.runtime)) and profile.gpu:
        request = request or ReproductionRequest(device="gpu")
    spec = spec.model_copy(
        update={
            "runtime": compute_runtime(settings, submission.intent, request, spec.runtime),
            "reproduction": request,
        }
    )
    if not spec.ablations and not spec.ablation_search:
        budget = requested_ablation_budget(submission.intent)
        if budget:
            spec = ResearchSpec(**{**spec.model_dump(), "ablation_search": budget.model_dump()})
    if submission.workflow == "benchmark" and spec.benchmark is None:
        raise ContractError("benchmark requires an uploaded dataset and explicit column mapping")
    if spec.repository is None and not submission.attachments and submission.code is None:
        raise ContractError("provide repository, paper attachments, or explicit experiment code")
    nodes = source_tasks(submission, spec, settings)
    if submission.auto_design:
        environment = nodes.pop()
        design = research_task(
            "research_design",
            [
                "research.design",
                *runtime_tools(settings),
                *(
                    ["environment.repair"]
                    if "environment.repair" in repair_tools(submission.intent)
                    else []
                ),
            ],
            ["research_spec", "experiment_design", "dependency_resolution_report"],
            settings,
        )
        design.inputs["research"] = spec.model_dump()
        design.inputs["code"] = submission.code
        depend(design, nodes)
        depend(environment, [design])
        nodes.extend([design, environment])
    if request:
        compute = compute_task(settings, request, spec.runtime)
        for node in nodes:
            if node.type in {"research_design", "environment_lock"}:
                depend(
                    node,
                    [
                        next(item for item in nodes if item.id == parent)
                        for parent in node.dependencies
                    ]
                    + [compute],
                )
        nodes.insert(0, compute)
    if spec.ablation_search:
        search = research_task(
            "ablation_design",
            ["ablation.design"],
            [
                "ablation_plan",
                "selected_ablation_configs",
                "ablation_selection_report",
                "ablation_research_spec",
            ],
            settings,
        )
        search.assigned_to = "data_agent"
        search.name = "ToT 消融候选评估与预算筛选"
        search.inputs["research"] = spec.model_dump()
        search.inputs["code"] = submission.code
        depend(search, [node for node in nodes if node.type != "environment_lock"])
        nodes.append(search)
    freeze = research_task("claim_freeze", ["claims.freeze"], ["claim_rubric"], settings)
    freeze.inputs["rubric"] = spec.rubric.model_dump()
    depend(
        freeze,
        [
            node
            for node in nodes
            if node.type
            in {
                "document_extract",
                "research_design",
                "ablation_design",
            }
        ],
    )
    nodes.append(freeze)
    prepare = research_task("research_prepare", ["research.patch"], ["research_patch"], settings)
    prepare.description = submission.intent
    prepare.inputs = {"research": spec.model_dump(), "code": submission.code}
    depend(prepare, nodes)
    execution_parents = [*nodes, prepare]
    preflight = None
    if spec.benchmark:
        preflight = research_task(
            "benchmark_preflight",
            ["benchmark.preflight", *repair_tools(submission.intent), *runtime_tools(settings)],
            ["benchmark_preflight_report", "benchmark_preflight_patch"],
            settings,
        )
        preflight.name = "Benchmark 样本预检"
        preflight.inputs["research"] = spec.model_dump()
        depend(preflight, execution_parents)
        execution_parents.append(preflight)
    execute = research_task(
        "research_execute",
        [
            "research.run",
            *runtime_tools(settings),
            *repair_tools(submission.intent),
            *(["benchmark.preflight"] if spec.benchmark else []),
        ],
        [
            "research_report",
            "benchmark_predictions",
            "final_patch",
            "claim_evidence_graph",
            "claim_verification_report",
        ],
        settings,
    )
    execute.inputs["research"] = spec.model_dump()
    depend(execute, execution_parents)
    nodes.extend([prepare, *([preflight] if preflight else []), execute])
    if request:
        for node in nodes:
            if node.type in {
                "research_design",
                "environment_lock",
                "research_prepare",
                "benchmark_preflight",
                "research_execute",
                "ablation_design",
            }:
                node.contract.allowed_tools.append("runtime.probe")
                if spec.runtime and "runtime.select" not in node.contract.allowed_tools:
                    node.contract.allowed_tools.append("runtime.select")
            if (
                node.type in {"benchmark_preflight", "research_execute"}
                and "reproduction_decision" not in node.required_artifacts
            ):
                depend(
                    node,
                    [
                        next(item for item in nodes if item.id == parent)
                        for parent in node.dependencies
                    ]
                    + [compute],
                )
    if submission.auto_design or request:
        summary = research_task("chat_answer", ["chat.answer"], ["chat_answer"], settings)
        summary.name = "实际执行报告"
        summary.assigned_to = "chat_agent"
        summary.description = "用中文返回本次真实测试、数值、失败与局限；不得声称未执行的论文效果。"
        # 只读报告等待全部节点；准备阶段失败也须把实际诊断回传聊天。
        summary.trigger_rule = "all_done"
        summary.dependencies = [node.id for node in nodes]
        nodes.append(summary)
    approval = Approval(
        required=settings.require_approval,
        status="pending" if settings.require_approval else "not_required",
    )
    return Plan(
        user_intent=submission.intent,
        intent_type="Benchmark" if spec.benchmark else "Paper_Reproduction",
        owner_id=submission.identity.user_id,
        session_id=submission.identity.session_id,
        attachments=submission.attachments,
        nodes=nodes,
        edges=[
            Edge(**{"from": parent, "to": node.id})
            for node in nodes
            for parent in node.dependencies
        ],
        approval=approval,
        status=Status.AWAITING_APPROVAL if approval.required else Status.PENDING,
        budget=Budget(
            max_task_attempts=settings.max_task_attempts,
            max_duration_seconds=settings.plan_timeout_seconds,
        ),
    )
