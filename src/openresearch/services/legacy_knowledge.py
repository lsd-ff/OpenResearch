from pydantic import ValidationError

from openresearch.errors import ContractError
from openresearch.models.context import Identity
from openresearch.models.discovery import SearchRequest
from openresearch.models.knowledge import KNOWLEDGE_TYPES
from openresearch.models.legacy import DirectExecutionRequest
from openresearch.models.runtime import Plan, Task
from openresearch.planner.documents import document_task
from openresearch.planner.research import depend, research_task
from openresearch.services.legacy_inputs import text_input, uploaded_inputs
from openresearch.services.legacy_plans import managed_legacy_plan
from openresearch.services.plans import PlanService
from openresearch.services.source_queries import named_source_query


def select_knowledge_stage(plan: Plan, payload: DirectExecutionRequest, kind: str) -> Task:
    if plan.intent_type not in {"General_Response", "Knowledge_Research"}:
        raise ContractError("knowledge stage requires an owned knowledge workflow")
    selected = text_input(payload.inputs, "stage_task_id", limit=32)
    candidates = [
        node
        for node in plan.nodes
        if node.type == KNOWLEDGE_TYPES[kind] and (selected is None or node.id == selected)
    ]
    if len(candidates) != 1:
        raise ContractError("knowledge stage is absent or incompatible with the selected workflow")
    return candidates[0]


def answer_task(kind: str, plans: PlanService) -> Task:
    key = "background_context" if kind == "knowledge_research" else "general_response"
    task = research_task(kind, ["knowledge.answer"], [key, kind + "_validation"], plans.settings)
    task.assigned_to = "librarian_agent" if kind == "knowledge_research" else "data_agent"
    return task


def build_knowledge_stage(
    payload: DirectExecutionRequest,
    identity: Identity,
    plans: PlanService,
    kind: str,
) -> tuple[Plan, str]:
    supported = {
        "attachments",
        "upload_id",
        "paper_upload_id",
        "paper_path",
        "pdf_url",
        "paper_url",
        "search",
        "stage_task_id",
    }
    if set(payload.inputs) - supported:
        raise ContractError(
            "knowledge requests accept source declarations, not raw background reports"
        )
    attachments = uploaded_inputs(payload)
    if len(set(attachments)) != len(attachments):
        raise ContractError("duplicate knowledge attachments")
    for identifier in attachments:
        plans.repository.uploads.get(identifier, identity.user_id)
    nodes = [document_task(plans.settings)] if attachments else []
    search = None
    try:
        if "search" in payload.inputs:
            search = SearchRequest.model_validate(payload.inputs["search"])
        elif not attachments and kind != "general_process":
            search = SearchRequest(
                kind="paper",
                query=named_source_query(payload.task_description)
                or payload.task_description[:240],
            )
    except ValidationError as exc:
        raise ContractError("invalid knowledge search declaration: " + str(exc)) from exc
    if search:
        discovery = research_task(
            "source_discover", ["sources.search"], ["discovered_sources"], plans.settings
        )
        discovery.assigned_to = "librarian_agent"
        discovery.inputs = search.model_dump()
        nodes.append(discovery)
    if kind == "general_process":
        process = answer_task("knowledge_process", plans)
        depend(process, nodes)
        nodes.append(process)
    else:
        research = answer_task("knowledge_research", plans)
        depend(research, nodes)
        synthesis = answer_task("knowledge_synthesis", plans)
        depend(synthesis, [*nodes, research])
        nodes.extend([research, synthesis])
    plan = managed_legacy_plan(payload.task_description, identity, plans, nodes)
    plan.attachments = attachments
    plan.intent_type = "General_Response" if kind == "general_process" else "Knowledge_Research"
    target = select_knowledge_stage(plan, payload, kind)
    if payload.task_name:
        target.name = payload.task_name
    return plan, target.id
