"""旧请求映射到服务端模板；外部任务 ID 仅作来源标记。"""

from typing import Any

from openresearch.errors import ContractError
from openresearch.models.audit import AUDIT_ROLES
from openresearch.models.compute import ReproductionRequest
from openresearch.models.context import Identity
from openresearch.models.knowledge import KNOWLEDGE_ROLES
from openresearch.models.legacy import DirectExecutionRequest, LegacyEnvironmentState
from openresearch.models.runtime import (
    Approval,
    Budget,
    Edge,
    LegacyTaskOrigin,
    Plan,
    Status,
    Task,
    TaskContract,
)
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.models.submissions import PlanSubmission, Workflow
from openresearch.planner.code import code_permissions
from openresearch.planner.compute import compute_task
from openresearch.planner.documents import prepend_documents
from openresearch.research.runtime_selection import bind_runtime, runtime_for_intent
from openresearch.services.legacy_environment import (
    ENV_ALIASES,
    ENV_KINDS,
    attach_environment,
    environment_node,
)
from openresearch.services.legacy_frameworks import framework_stage
from openresearch.services.legacy_inputs import legacy_requirements, text_input, uploaded_inputs
from openresearch.services.legacy_stages import STAGES
from openresearch.services.plans import PlanService

CODE_KINDS = {"generate_code", "code_execution", "execute_code", "baseline_run", "run_benchmark"}
SUPPORTED_KINDS = (
    CODE_KINDS
    | ENV_KINDS
    | STAGES.keys()
    | AUDIT_ROLES.keys()
    | KNOWLEDGE_ROLES.keys()
    | {"dataset_profile", "paper_parse"}
)
ALIASES = {
    "code_generate": "generate_code",
    "code_execute": "execute_code",
    "data_analyze": "dataset_profile",
    "document_extract": "paper_parse",
}


def legacy_kind(payload: DirectExecutionRequest) -> str:
    kind = payload.task_type.strip().lower()
    if kind:
        return ENV_ALIASES.get(kind, ALIASES.get(kind, kind))
    return {
        "sandbox_agent": "execute_code",
        "data_agent": "dataset_profile",
        "librarian_agent": "paper_parse",
    }.get(payload.assigned_to, "generate_code")


def validate_legacy_request(payload: DirectExecutionRequest) -> str:
    kind = legacy_kind(payload)
    framework = framework_stage(kind)
    if framework is None and kind not in SUPPORTED_KINDS:
        raise ContractError(
            "legacy operation requires a managed continuation or is not yet mapped: " + kind
        )
    if not payload.task_description.strip():
        raise ContractError("legacy direct tasks require task_description")
    roles = (
        {"coder_agent", "sandbox_agent"}
        if kind in CODE_KINDS | ENV_KINDS
        else (
            {"research_coding_agent", "data_agent"}
            if kind == "dataset_profile"
            else {"librarian_agent", "data_agent"}
        )
    )
    if kind == "generate_code":
        roles = {"coder_agent"}
    if kind in STAGES:
        roles = set(STAGES[kind].roles)
    if kind in AUDIT_ROLES:
        roles = AUDIT_ROLES[kind]
    if framework:
        roles = set(framework.roles)
    if kind in KNOWLEDGE_ROLES:
        roles = KNOWLEDGE_ROLES[kind]
    if payload.assigned_to and payload.assigned_to not in roles:
        raise ContractError("legacy task role is incompatible with its declared operation")
    return kind


def prefix_plan(plan: Plan, target_type: str) -> Plan:
    target = next((node for node in reversed(plan.nodes) if node.type == target_type), None)
    if target is None:
        raise ContractError("legacy task has no matching managed execution stage")
    selected = {target.id}
    while (
        extra := {
            parent for node in plan.nodes if node.id in selected for parent in node.dependencies
        }
        - selected
    ):
        selected.update(extra)
    return Plan.model_validate(
        {
            **plan.model_dump(by_alias=True),
            "nodes": [node.model_dump() for node in plan.nodes if node.id in selected],
            "edges": [
                edge.model_dump(by_alias=True)
                for edge in plan.edges
                if edge.from_ in selected and edge.to in selected
            ],
        }
    )


def basic_legacy_plan(
    payload: DirectExecutionRequest,
    identity: Identity,
    plans: PlanService,
    state: LegacyEnvironmentState | None = None,
) -> Plan:
    kind = validate_legacy_request(payload)
    attachments = uploaded_inputs(payload)
    if kind in CODE_KINDS:
        plan = _code_plan(payload, identity, plans, state)
    elif kind in ENV_KINDS:
        plan = _environment_plan(payload, identity, plans, state)
    elif kind in {"dataset_profile", "paper_parse"}:
        workflow: Workflow = "data" if kind == "dataset_profile" else "paper"
        plan = plans.build(
            PlanSubmission(
                payload.task_description, identity, attachments=attachments, workflow=workflow
            )
        )
        if kind == "paper_parse":
            plan = prefix_plan(plan, "document_extract")
    else:
        raise ContractError("operation requires its managed scientific or review service")
    if payload.task_name:
        plan.nodes[-1].name = payload.task_name
    plan.legacy = LegacyTaskOrigin(
        task_id=payload.task_id,
        task_type=kind,
        requested_type=payload.task_type,
        task_name=payload.task_name,
        assigned_to=payload.assigned_to,
        native_task_id=plan.nodes[-1].id,
    )
    return plan


def _code_plan(
    payload: DirectExecutionRequest,
    identity: Identity,
    plans: PlanService,
    state: LegacyEnvironmentState | None,
) -> Plan:
    if legacy_kind(payload) == "generate_code":
        return _generation_plan(payload, identity, plans, state)
    code = text_input(payload.inputs, "generated_code", "code")
    if legacy_kind(payload) != "code_execution" and not code:
        raise ContractError("legacy execution requires generated_code or code")
    plan = plans.build(
        PlanSubmission(payload.task_description, identity, code, uploaded_inputs(payload), "code")
    )
    plan = prefix_plan(plan, "code_execute")
    if (
        state
        or legacy_requirements(payload.inputs) is not None
        or payload.inputs.get("runtime_profile")
    ):
        plan = attach_environment(plan, payload, plans.settings, state)
    return plan


def _generation_plan(
    payload: DirectExecutionRequest,
    identity: Identity,
    plans: PlanService,
    state: LegacyEnvironmentState | None,
) -> Plan:
    profile = text_input(payload.inputs, "runtime_profile", limit=32)
    binding = bind_runtime(plans.settings, profile) if profile else state.runtime if state else None
    binding = runtime_for_intent(plans.settings, payload.task_description, binding, allow_gpu=True)
    tools = code_permissions(payload.task_description)[0]
    inputs: dict[str, Any] = {
        "code": text_input(payload.inputs, "generated_code", "code"),
        "legacy_requirements": legacy_requirements(payload.inputs)
        or (state.spec.requirements if state else []),
    }
    if binding:
        inputs["runtime"] = binding.model_dump()
        tools.extend(["runtime.select", "runtime.probe"])
    outputs = ["generated_code", "code_preparation_report"]
    node = Task(
        name="Generate legacy Python code",
        type="code_generate",
        assigned_to="coder_agent",
        description=payload.task_description,
        inputs=inputs,
        output_artifacts=outputs,
        timeout_seconds=plans.settings.task_timeout_seconds,
        contract=TaskContract(allowed_tools=tools, output_artifacts=outputs),
    )
    plan = managed_legacy_plan(payload.task_description, identity, plans, [node])
    plan.attachments = uploaded_inputs(payload)
    for identifier in plan.attachments:
        plans.repository.uploads.get(identifier, identity.user_id)
    return prepend_documents(plan, plans.settings) if plan.attachments else plan


def _environment_plan(
    payload: DirectExecutionRequest,
    identity: Identity,
    plans: PlanService,
    state: LegacyEnvironmentState | None,
) -> Plan:
    kind = legacy_kind(payload)
    if kind == "install_dependencies" and state is None:
        raise ContractError("dependency installation requires an owned prepared runtime reference")
    node = environment_node(
        payload,
        plans.settings,
        {
            "resolve_dependencies": "resolve",
            "prepare_runtime": "prepare",
            "install_dependencies": "install",
        }[kind],
        state,
    )
    nodes = [node]
    if node.inputs.get("reproduction"):
        binding = (
            RuntimeBinding.model_validate(node.inputs["runtime"])
            if node.inputs.get("runtime")
            else None
        )
        compute = compute_task(
            plans.settings, ReproductionRequest.model_validate(node.inputs["reproduction"]), binding
        )
        node.dependencies = [compute.id]
        nodes.insert(0, compute)
    return managed_legacy_plan(payload.task_description, identity, plans, nodes)


def managed_legacy_plan(
    intent: str, identity: Identity, plans: PlanService, nodes: list[Task]
) -> Plan:
    settings = plans.settings
    return Plan(
        user_intent=intent,
        owner_id=identity.user_id,
        session_id=identity.session_id,
        nodes=nodes,
        edges=[
            Edge(**{"from": parent, "to": node.id})
            for node in nodes
            for parent in node.dependencies
        ],
        approval=Approval(
            required=settings.require_approval,
            status="pending" if settings.require_approval else "not_required",
        ),
        status=Status.AWAITING_APPROVAL if settings.require_approval else Status.PENDING,
        budget=Budget(
            max_task_attempts=settings.max_task_attempts,
            max_duration_seconds=settings.plan_timeout_seconds,
        ),
    )
