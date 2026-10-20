"""框架旧入口推进同一冻结比较图；分支编号只选择已有的实际节点。"""

import re
from dataclasses import dataclass

from pydantic import ValidationError

from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.comparison import ComparisonSpec, FrameworkTarget
from openresearch.models.context import Identity
from openresearch.models.legacy import DirectExecutionRequest
from openresearch.models.planning import IntentDraft
from openresearch.models.runtime import Edge, Plan, Task
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.framework import namespace_task
from openresearch.planner.research import depend, research_task
from openresearch.services.intent import named_comparison
from openresearch.services.legacy_inputs import text_input, uploaded_inputs
from openresearch.services.plans import PlanService


@dataclass(frozen=True)
class FrameworkStage:
    native_type: str
    roles: frozenset[str]
    index: int | None = None


def framework_stage(kind: str) -> FrameworkStage | None:
    if kind == "framework_research":
        return FrameworkStage("framework_catalog", frozenset({"librarian_agent"}))
    if kind in {"framework_recommendation", "framework_report"}:
        return FrameworkStage("framework_report", frozenset({"data_agent"}))
    match = re.fullmatch(r"framework_(fit|prepare|run)_(left|right|[1-4])", kind)
    if match is None:
        return None
    index = {"left": 1, "right": 2}.get(match[2])
    index = int(match[2]) if index is None else index
    native, roles = {
        "fit": ("framework_research", {"librarian_agent"}),
        "prepare": ("research_prepare", {"coder_agent"}),
        "run": ("research_execute", {"sandbox_agent", "research_coding_agent", "coder_agent"}),
    }[match[1]]
    return FrameworkStage(native, frozenset(roles), index)


async def framework_spec(payload: DirectExecutionRequest, plans: PlanService) -> ComparisonSpec:
    if set(payload.inputs) - {"comparison", "frameworks", "attachments", "stage_task_id"}:
        raise ContractError("framework stages accept declarations, not raw reports or environments")
    try:
        explicit = payload.inputs.get("comparison")
        names = payload.inputs.get("frameworks")
        if explicit is not None:
            spec = ComparisonSpec.model_validate(explicit)
            if names is not None and names != [target.name for target in spec.targets]:
                raise ContractError("conflicting framework declarations")
            return spec
        draft = named_comparison(payload.task_description)
        if names is None and draft is None:
            draft = await LLMClient(plans.settings).request_typed(
                [
                    {
                        "role": "system",
                        "content": "Extract the two to four software framework names explicitly "
                        "named by the human. Return IntentDraft with workflow=framework and names "
                        "copied from the text and framework_mode=research or benchmark. "
                        "Do not invent targets, packages, metrics or results.",
                    },
                    {"role": "user", "content": payload.task_description},
                ],
                IntentDraft,
            )
            normalized = re.sub(r"[\W_]+", "", payload.task_description.casefold())
            if draft.workflow != "framework" or any(
                not re.sub(r"[\W_]+", "", name.casefold())
                or re.sub(r"[\W_]+", "", name.casefold()) not in normalized
                for name in draft.frameworks
            ):
                raise ContractError("framework names must come from the human's request")
        if names is None:
            assert draft is not None
            names = draft.frameworks
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ContractError("frameworks must be a list of explicitly named targets")
        mode = draft.framework_mode if draft else "research"
        kind = payload.task_type.strip().lower()
        if kind.startswith(("framework_prepare_", "framework_run_")) or kind == "framework_report":
            mode = "benchmark"
        return ComparisonSpec(targets=[FrameworkTarget(name=name) for name in names], mode=mode)
    except ValidationError as exc:
        raise ContractError("invalid framework comparison declaration: " + str(exc)) from exc


def comparison_spec(plan: Plan) -> ComparisonSpec:
    protocols = [node for node in plan.nodes if node.type == "comparison_protocol"]
    if plan.intent_type != "Framework_Comparison" or len(protocols) != 1:
        raise ContractError("framework stage requires an owned comparison workflow")
    return ComparisonSpec.model_validate(protocols[0].inputs["comparison"])


def select_framework_stage(plan: Plan, payload: DirectExecutionRequest, kind: str) -> Task:
    rule = framework_stage(kind)
    if rule is None:
        raise ContractError("unknown framework stage")
    spec = comparison_spec(plan)
    if kind == "framework_recommendation" and spec.mode != "research":
        raise ContractError("framework recommendation requires a source research comparison")
    if rule.native_type in {"research_prepare", "research_execute"} and spec.mode != "benchmark":
        raise ContractError("framework execution requires a frozen benchmark comparison")
    if rule.index is not None and rule.index > len(spec.targets):
        raise ContractError("framework branch is absent from the frozen comparison")
    selected = text_input(payload.inputs, "stage_task_id", limit=32)
    candidates = [
        task
        for task in plan.nodes
        if task.type == rule.native_type
        and (rule.index is None or task.artifact_scope == f"framework_{rule.index}")
        and (selected is None or task.id == selected)
    ]
    if len(candidates) != 1:
        raise ContractError("framework stage is absent, ambiguous or selects another branch")
    return candidates[0]


def add_catalog(plan: Plan, plans: PlanService, spec: ComparisonSpec) -> None:
    protocol = next(node for node in plan.nodes if node.type == "comparison_protocol")
    report = next(node for node in plan.nodes if node.type == "framework_report")
    studies = [node for node in plan.nodes if node.type == "framework_research"]
    if not studies:
        for index, target in enumerate(spec.targets, 1):
            study = research_task(
                "framework_research",
                ["framework.research"],
                ["framework_research_report"],
                plans.settings,
            )
            study.assigned_to = "librarian_agent"
            study.name = target.name + " · 来源与适用性"
            study.inputs["target"] = target.model_dump()
            depend(study, [protocol])
            namespace_task(study, f"framework_{index}")
            studies.append(study)
        plan.nodes[1:1] = studies
    catalog = research_task(
        "framework_catalog", ["comparison.catalog"], ["framework_catalog"], plans.settings
    )
    catalog.assigned_to = "data_agent"
    catalog.name = "冻结框架来源目录"
    catalog.inputs["comparison"] = spec.model_dump()
    depend(catalog, studies)
    plan.nodes.insert(plan.nodes.index(report), catalog)
    for task in plan.nodes:
        if task.artifact_scope:
            task.inputs["framework_index"] = int(task.artifact_scope.removeprefix("framework_"))
        if task.type == "framework_design":
            task.dependencies.append(catalog.id)
            task.required_artifacts.append("framework_catalog")
            task.contract.input_artifacts = list(task.required_artifacts)
    report.dependencies = [task.id for task in plan.nodes if task.id != report.id]
    plan.edges = [
        Edge(**{"from": parent, "to": task.id})
        for task in plan.nodes
        for parent in task.dependencies
    ]
    if len(plan.nodes) > plan.budget.max_task_attempts:
        raise ContractError("framework stages exceed the configured attempt budget")


def build_framework_stage(
    payload: DirectExecutionRequest,
    identity: Identity,
    plans: PlanService,
    kind: str,
    spec: ComparisonSpec,
) -> tuple[Plan, str]:
    plan = plans.build(
        PlanSubmission(
            payload.task_description,
            identity,
            attachments=uploaded_inputs(payload),
            workflow="framework",
            comparison=spec,
        )
    )
    add_catalog(plan, plans, spec)
    target = select_framework_stage(plan, payload, kind)
    if payload.task_name:
        target.name = payload.task_name
    return plan, target.id
