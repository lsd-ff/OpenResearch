"""旧科研阶段推进同一份受管图，不导入客户端声称的执行产物。"""

import re
from dataclasses import dataclass

from pydantic import ValidationError

from openresearch.errors import ContractError
from openresearch.models.context import Identity
from openresearch.models.graph import ancestor_map
from openresearch.models.legacy import DirectExecutionRequest
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Plan, Task
from openresearch.models.sources import EnvironmentSpec, RepositorySpec
from openresearch.models.submissions import PlanSubmission
from openresearch.research.runtime_selection import bind_runtime
from openresearch.services.legacy_inputs import legacy_requirements, text_input, uploaded_inputs
from openresearch.services.plans import PlanService


@dataclass(frozen=True)
class StageRule:
    native_type: str
    roles: frozenset[str]
    benchmark: bool = False
    paper: bool = False
    repository: bool = False


STAGES = {
    "claim_rubric_extract": StageRule(
        "claim_freeze", frozenset({"librarian_agent", "data_agent"}), paper=True
    ),
    "repo_prepare": StageRule("research_prepare", frozenset({"coder_agent"}), repository=True),
    "ablation_design": StageRule("ablation_design", frozenset({"data_agent"})),
    "benchmark_adapter_generate": StageRule(
        "research_prepare", frozenset({"research_coding_agent", "coder_agent"}), benchmark=True
    ),
    "benchmark_adapter_preflight": StageRule(
        "benchmark_preflight", frozenset({"research_coding_agent", "coder_agent"}), benchmark=True
    ),
    "benchmark_execute": StageRule(
        "research_execute", frozenset({"research_coding_agent", "sandbox_agent"}), benchmark=True
    ),
    "paper_code_execute": StageRule(
        "research_execute", frozenset({"research_coding_agent", "coder_agent"})
    ),
}


def workflow_plan_id(reference: str) -> str:
    match = re.fullmatch(r"openresearch:([a-f0-9]{32})", reference)
    if match is None:
        raise ContractError("workflow_ref requires an owned openresearch plan reference")
    return match[1]


def select_stage(plan: Plan, payload: DirectExecutionRequest, kind: str) -> Task:
    rule = STAGES[kind]
    selected_id = text_input(payload.inputs, "stage_task_id", limit=32)
    candidates = [
        task
        for task in plan.nodes
        if task.type == rule.native_type and (selected_id is None or task.id == selected_id)
    ]
    if len(candidates) != 1:
        raise ContractError(
            "stage is absent or ambiguous; select an actual compatible stage_task_id"
        )
    target = candidates[0]
    ancestors = ancestor_map({task.id: task for task in plan.nodes})[target.id]
    if rule.paper and not any(
        task.id in ancestors and "parsed_paper" in task.output_artifacts for task in plan.nodes
    ):
        raise ContractError("claim extraction requires actual paper pages in the managed workflow")
    if rule.repository and not any(
        task.id in ancestors and task.type == "repository_fetch" for task in plan.nodes
    ):
        raise ContractError("repository preparation requires a pinned repository source stage")
    if rule.benchmark and not any(
        task.type == "benchmark_preflight" and task.artifact_scope == target.artifact_scope
        for task in plan.nodes
    ):
        raise ContractError("benchmark stage requires a frozen dataset and benchmark protocol")
    return target


def build_stage_plan(
    payload: DirectExecutionRequest, identity: Identity, plans: PlanService, kind: str
) -> tuple[Plan, str]:
    spec = stage_spec(payload, plans)
    code = text_input(payload.inputs, "generated_code", "code")
    auto_design = payload.inputs.get("auto_design", False)
    if not isinstance(auto_design, bool):
        raise ContractError("auto_design must be a boolean")
    plan = plans.build(
        PlanSubmission(
            payload.task_description,
            identity,
            code,
            uploaded_inputs(payload),
            "benchmark" if spec.benchmark else "reproduce",
            spec,
            auto_design,
        )
    )
    target = select_stage(plan, payload, kind)
    if payload.task_name:
        target.name = payload.task_name
    return plan, target.id


def stage_spec(payload: DirectExecutionRequest, plans: PlanService) -> ResearchSpec:
    supported = {
        "research",
        "generated_code",
        "code",
        "auto_design",
        "stage_task_id",
        "attachments",
        "upload_id",
        "dataset_upload_id",
        "paper_upload_id",
        "dataset_path",
        "paper_path",
        "pdf_url",
        "paper_url",
        "repo_url",
        "runtime_profile",
        "dependency_spec",
        "requirements",
        "dependencies",
    }
    if set(payload.inputs) - supported:
        raise ContractError(
            "scientific stages accept declarations or workflow_ref, not raw execution artifacts"
        )
    try:
        spec = ResearchSpec.model_validate(payload.inputs.get("research", {}))
        requirements = legacy_requirements(payload.inputs)
        if requirements is not None:
            environment = EnvironmentSpec(
                requirements=requirements, interpreter=spec.environment.interpreter
            )
            if spec.environment.requirements and set(spec.environment.requirements) != set(
                environment.requirements
            ):
                raise ContractError("conflicting scientific dependency declarations")
            spec.environment = environment
        if url := text_input(payload.inputs, "repo_url", limit=4096):
            repository = RepositorySpec(url=url)
            if spec.repository and spec.repository != repository:
                raise ContractError("conflicting scientific repository declarations")
            spec.repository = repository
        if profile := text_input(payload.inputs, "runtime_profile", limit=32):
            runtime = bind_runtime(plans.settings, profile)
            if spec.runtime and spec.runtime != runtime:
                raise ContractError("conflicting scientific runtime declarations")
            spec.runtime = runtime
        return ResearchSpec.model_validate(spec.model_dump())
    except ValidationError as exc:
        raise ContractError("invalid managed research specification: " + str(exc)) from exc
