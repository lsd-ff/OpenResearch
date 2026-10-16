from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.runtime import Approval, Budget, Edge, Plan, Status, Task, TaskContract
from openresearch.models.submissions import PlanSubmission


def document_task(settings: Settings) -> Task:
    return Task(
        name="Extract Source Documents",
        type="document_extract",
        assigned_to="librarian_agent",
        description="Extract page-addressable text and source links from the supplied documents.",
        timeout_seconds=settings.document_timeout_seconds,
        output_artifacts=["parsed_paper", "source_index"],
        contract=TaskContract(
            output_artifacts=["parsed_paper", "source_index"], allowed_tools=["documents.extract"]
        ),
    )


def build_document_plan(submission: PlanSubmission, settings: Settings) -> Plan:
    if not submission.attachments:
        raise ContractError("paper analysis requires at least one uploaded source document")
    approval = Approval(
        required=settings.require_approval,
        status="pending" if settings.require_approval else "not_required",
    )
    extraction = document_task(settings)
    nodes = [extraction]
    edges = []
    if submission.auto_design:
        analysis = Task(
            name="Analyze Source Documents",
            type="paper_analyze",
            assigned_to="librarian_agent",
            description=submission.intent,
            dependencies=[extraction.id],
            required_artifacts=["parsed_paper"],
            output_artifacts=["paper_analysis"],
            timeout_seconds=settings.task_timeout_seconds,
            contract=TaskContract(
                input_artifacts=["parsed_paper"],
                output_artifacts=["paper_analysis"],
                allowed_tools=["documents.answer"],
            ),
        )
        nodes.append(analysis)
        edges.append(Edge(**{"from": extraction.id, "to": analysis.id}))
    return Plan(
        user_intent=submission.intent,
        owner_id=submission.identity.user_id,
        session_id=submission.identity.session_id,
        intent_type="Paper_Analysis",
        attachments=submission.attachments,
        nodes=nodes,
        edges=edges,
        approval=approval,
        status=Status.AWAITING_APPROVAL if approval.required else Status.PENDING,
        budget=Budget(
            max_task_attempts=settings.max_task_attempts,
            max_duration_seconds=settings.plan_timeout_seconds,
        ),
    )


def prepend_documents(plan: Plan, settings: Settings) -> Plan:
    extraction = document_task(settings)
    generation = plan.nodes[0]
    generation.dependencies.append(extraction.id)
    generation.required_artifacts.append("parsed_paper")
    generation.contract.input_artifacts.append("parsed_paper")
    plan.nodes.insert(0, extraction)
    plan.edges.append(Edge(**{"from": extraction.id, "to": generation.id}))
    return Plan.model_validate(plan.model_dump(mode="json", by_alias=True))
