import re
from typing import Any

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.compute import ReproductionRequest
from openresearch.models.legacy import DirectExecutionRequest, LegacyEnvironmentState
from openresearch.models.runtime import Edge, Plan, Task, TaskContract
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.planner.code import code_permissions
from openresearch.planner.compute import compute_task
from openresearch.research.compute import compute_runtime
from openresearch.research.reproduction_mode import request_from_intent
from openresearch.research.runtime_selection import bind_runtime, bound_profile, runtime_tools
from openresearch.services.legacy_inputs import legacy_requirements, text_input
from openresearch.sources.dependency_resolution import dependency_requests

ENV_KINDS = {"resolve_dependencies", "prepare_runtime", "install_dependencies"}
ENV_ALIASES = {
    "env_setup": "prepare_runtime",
    "prepare_environment": "prepare_runtime",
    "setup_runtime": "prepare_runtime",
    "test_environment": "prepare_runtime",
    "dependency_install": "install_dependencies",
}


def environment_binding(
    payload: DirectExecutionRequest, settings: Settings, state: LegacyEnvironmentState | None
) -> tuple[RuntimeBinding | None, ReproductionRequest | None]:
    profile = text_input(payload.inputs, "runtime_profile", limit=32)
    binding = bind_runtime(settings, profile) if profile else state.runtime if state else None
    if state and binding != state.runtime:
        raise ContractError("prepared runtime cannot be silently replaced by another profile")
    request = request_from_intent(payload.task_description)
    if binding and (configured := bound_profile(settings, binding)) and configured.gpu:
        request = request or ReproductionRequest(device="gpu")
    binding = compute_runtime(settings, payload.task_description, request, binding)
    return binding, request


def environment_node(
    payload: DirectExecutionRequest,
    settings: Settings,
    mode: str,
    state: LegacyEnvironmentState | None = None,
) -> Task:
    requirements = legacy_requirements(payload.inputs)
    if requirements is not None:
        requirements = dependency_requests(requirements, limit=128)
    if (
        requirements is not None
        and re.search(
            r"(?:不要|禁止|不允许)安装",
            re.sub(r"```.*?```", "", payload.task_description, flags=re.S),
        )
        and requirements
    ):
        raise ContractError("explicit dependency request conflicts with the no-install instruction")
    binding, request = environment_binding(payload, settings, state)
    if (
        state
        and requirements is not None
        and set(requirements) != set(state.requests)
        and (state.spec.requirements or state.requests)
    ):
        raise ContractError("prepared dependencies changed; prepare a new environment explicitly")
    tools = ["environment.lock", "runtime.version", *runtime_tools(settings)]
    if "environment.lock" in code_permissions(payload.task_description)[1]:
        tools.append("environment.repair")
    if binding and "runtime.select" not in tools:
        tools.append("runtime.select")
    inputs: dict[str, Any] = {
        "mode": mode,
        "requirements": requirements,
        "code": text_input(payload.inputs, "generated_code", "code"),
        "explicit_requirements": requirements is not None,
    }
    if binding:
        inputs["runtime"] = binding.model_dump()
    if request:
        tools.append("runtime.probe")
        inputs["reproduction"] = request.model_dump()
    if state:
        inputs["initial_environment"] = state.model_dump()
        if requirements is None:
            inputs["requirements"] = state.requests
    outputs = ["legacy_environment_state"] + (
        [] if mode == "restore" else ["environment_preparation_report"]
    )
    return Task(
        name="Managed legacy environment",
        type="legacy_environment",
        assigned_to="coder_agent",
        description=payload.task_description,
        inputs=inputs,
        output_artifacts=outputs,
        timeout_seconds=settings.task_timeout_seconds,
        contract=TaskContract(allowed_tools=tools, output_artifacts=outputs),
    )


def attach_environment(
    plan: Plan,
    payload: DirectExecutionRequest,
    settings: Settings,
    state: LegacyEnvironmentState | None,
) -> Plan:
    node = environment_node(
        payload, settings, "restore" if state and state.phase != "probe" else "resolve", state
    )
    generate = next(task for task in plan.nodes if task.type == "code_generate")
    execute = next(task for task in plan.nodes if task.type == "code_execute")
    if node.inputs["requirements"] is None and state is None:
        node.dependencies = [generate.id]
        node.required_artifacts = node.contract.input_artifacts = ["generated_code"]
    binding, request = environment_binding(payload, settings, state)
    for task in (generate, execute):
        if binding:
            task.inputs["runtime"] = binding.model_dump()
            if "runtime.select" not in task.contract.allowed_tools:
                task.contract.allowed_tools.append("runtime.select")
        if request:
            task.inputs["reproduction"] = request.model_dump()
            if "runtime.probe" not in task.contract.allowed_tools:
                task.contract.allowed_tools.append("runtime.probe")
    compute = next((task for task in plan.nodes if task.type == "runtime_prepare"), None)
    if request and compute is None:
        compute = compute_task(settings, request, binding)
        plan.nodes.insert(0, compute)
        generate.dependencies.append(compute.id)
    if compute:
        if binding:
            compute.inputs["runtime"] = binding.model_dump()
            if "runtime.select" not in compute.contract.allowed_tools:
                compute.contract.allowed_tools.append("runtime.select")
        node.dependencies.append(compute.id)
    execute.dependencies.append(node.id)
    execute.required_artifacts.append("legacy_environment_state")
    execute.contract.input_artifacts = list(execute.required_artifacts)
    execute.inputs["legacy_environment"] = True
    plan.nodes.insert(plan.nodes.index(execute), node)
    plan.edges = [
        Edge(**{"from": parent, "to": task.id})
        for task in plan.nodes
        for parent in task.dependencies
    ]
    return Plan.model_validate(plan.model_dump(by_alias=True))
