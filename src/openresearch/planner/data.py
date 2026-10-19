from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.runtime import Approval, Budget, Plan, Status, Task, TaskContract
from openresearch.models.submissions import PlanSubmission


def build_data_plan(submission: PlanSubmission, settings: Settings) -> Plan:
    if len(submission.attachments) != 1:
        raise ContractError("data analysis requires one explicitly selected uploaded dataset")
    task = Task(
        name="Analyze Uploaded Dataset",
        type="data_analyze",
        assigned_to="data_agent",
        description=submission.intent,
        inputs={"upload_id": submission.attachments[0]},
        output_artifacts=["data_profile", "data_chart"],
        timeout_seconds=settings.task_timeout_seconds,
        contract=TaskContract(
            output_artifacts=["data_profile", "data_chart"], allowed_tools=["data.profile"]
        ),
    )
    return Plan(
        user_intent=submission.intent,
        intent_type="Data_Analysis",
        owner_id=submission.identity.user_id,
        session_id=submission.identity.session_id,
        attachments=submission.attachments,
        nodes=[task],
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
