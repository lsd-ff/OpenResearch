from typing import Any

from fastapi import APIRouter

from openresearch.api.dependencies import IdentityDep, RuntimeDep
from openresearch.api.payloads import message_payload, plan_payload
from openresearch.api.schemas import CreatePlanRequest
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.agent import PlannerAgent

router = APIRouter(prefix="/api", tags=["plans"])


@router.post("/plan")
async def create_plan(
    payload: CreatePlanRequest, runtime: RuntimeDep, identity: IdentityDep
) -> dict[str, Any]:
    """创建并校验计划；执行由独立 Worker 领取。"""
    submission = PlanSubmission(
        intent=payload.intent,
        identity=identity,
        code=payload.code,
        attachments=payload.attachments,
        workflow=payload.workflow,
        research=payload.research,
    )
    if payload.workflow == "auto" and payload.research is None and payload.code is None:
        result = await runtime.messages.send(submission)
        response = message_payload(result)
        return {
            **response,
            "message": result.response,
            "user_id": identity.user_id,
            "anon_user_id": identity.user_id,
            "session_id": identity.session_id,
        }
    plan = runtime.plans.build(submission)
    if payload.planning_mode == "llm":
        plan = await PlannerAgent(runtime.settings, runtime.repository).refine(plan)
    runtime.repository.save_plan(plan)
    return {
        "message": "Plan generated successfully",
        "plan_graph": plan_payload(plan),
        "user_id": identity.user_id,
        "anon_user_id": identity.user_id,
        "session_id": identity.session_id,
        "intent_context": {
            "raw_intent": payload.intent,
            "intent_type": plan.intent_type,
            "entities": {},
            "constraints": {},
            "metadata": {},
        },
    }


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, runtime: RuntimeDep, identity: IdentityDep) -> dict[str, Any]:
    return {"plan_graph": plan_payload(runtime.repository.get_plan(plan_id, identity.user_id))}


@router.post("/plans/{plan_id}/execute", status_code=202)
def execute_plan(plan_id: str, runtime: RuntimeDep, identity: IdentityDep) -> dict[str, str]:
    runtime.repository.queue_plan(plan_id, identity.user_id)
    return {"message": "Plan queued for worker execution", "plan_id": plan_id}


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, runtime: RuntimeDep, identity: IdentityDep) -> dict[str, str]:
    runtime.repository.approve(plan_id, identity.user_id)
    return {"message": "Plan approved", "plan_id": plan_id}


@router.post("/plans/{plan_id}/cancel")
def cancel_plan(plan_id: str, runtime: RuntimeDep, identity: IdentityDep) -> dict[str, str]:
    runtime.repository.cancel(plan_id, identity.user_id)
    return {"message": "Plan canceled", "plan_id": plan_id}


@router.post("/plans/{plan_id}/tasks/{task_id}/retry")
def retry_task(
    plan_id: str, task_id: str, runtime: RuntimeDep, identity: IdentityDep
) -> dict[str, str]:
    runtime.repository.retry(plan_id, task_id, identity.user_id)
    return {"message": "Task and descendants reset", "plan_id": plan_id}
