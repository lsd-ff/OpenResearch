from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Approval, Budget, Edge, Plan, Status, Task, TaskContract
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.documents import document_task


def research_task(kind: str, tools: list[str], outputs: list[str], settings: Settings) -> Task:
    return Task(
        name=kind.replace("_", " ").title(),
        type=kind,
        assigned_to="research_agent",
        timeout_seconds=settings.task_timeout_seconds,
        output_artifacts=outputs,
        contract=TaskContract(output_artifacts=outputs, allowed_tools=tools),
    )


def depend(task: Task, parents: list[Task]) -> None:
    task.dependencies = [parent.id for parent in parents]
    task.required_artifacts = [key for parent in parents for key in parent.output_artifacts]
    task.contract.input_artifacts = task.required_artifacts


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
        "environment_lock", ["environment.lock"], ["environment_lock"], settings
    )
    environment.inputs["environment"] = spec.environment.model_dump()
    return [*nodes, environment]


def build_research_plan(submission: PlanSubmission, settings: Settings) -> Plan:
    spec = submission.research or ResearchSpec()
    if submission.workflow == "benchmark" and spec.benchmark is None:
        raise ContractError("benchmark requires an uploaded dataset and explicit column mapping")
    if spec.repository is None and not submission.attachments and submission.code is None:
        raise ContractError("provide repository, paper attachments, or explicit experiment code")
    nodes = source_tasks(submission, spec, settings)
    if submission.auto_design:
        environment = nodes.pop()
        design = research_task(
            "research_design", ["research.design"], ["research_spec", "experiment_design"], settings
        )
        design.inputs["research"] = spec.model_dump()
        design.inputs["code"] = submission.code
        depend(design, nodes)
        depend(environment, [design])
        nodes.extend([design, environment])
    freeze = research_task("claim_freeze", ["claims.freeze"], ["claim_rubric"], settings)
    freeze.inputs["rubric"] = spec.rubric.model_dump()
    depend(freeze, [node for node in nodes if node.type == "document_extract"])
    nodes.append(freeze)
    prepare = research_task("research_prepare", ["research.patch"], ["research_patch"], settings)
    prepare.description = submission.intent
    prepare.inputs = {"research": spec.model_dump(), "code": submission.code}
    depend(prepare, nodes)
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
    execute.inputs["research"] = spec.model_dump()
    depend(execute, [*nodes, prepare])
    nodes.extend([prepare, execute])
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
