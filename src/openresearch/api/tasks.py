import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from openresearch.api.dependencies import IdentityDep, Runtime, RuntimeDep
from openresearch.api.legacy_stream import legacy_events, task_result_payload
from openresearch.api.payloads import plan_payload
from openresearch.models.legacy import DirectExecutionRequest
from openresearch.models.runtime import ContractModel, Status
from openresearch.services.legacy_execution import LegacyExecutionService

router = APIRouter(prefix="/api", tags=["tasks"])


class ReassignRequest(ContractModel):
    assigned_to: str


@router.post("/plans/{plan_id}/tasks/{task_id}/execute", status_code=202)
def queue_task(
    plan_id: str, task_id: str, runtime: RuntimeDep, identity: IdentityDep
) -> dict[str, str]:
    runtime.repository.tasks.queue(plan_id, task_id, identity.user_id)
    return {"plan_id": plan_id, "task_id": task_id, "message": "Task queued"}


@router.post("/plans/{plan_id}/tasks/{task_id}/reassign")
def reassign_task(
    plan_id: str, task_id: str, payload: ReassignRequest, runtime: RuntimeDep, identity: IdentityDep
) -> dict[str, str]:
    runtime.repository.tasks.reassign(plan_id, task_id, payload.assigned_to, identity.user_id)
    return {"plan_id": plan_id, "task_id": task_id, "assigned_to": payload.assigned_to}


async def direct_events(
    request: Request, runtime: Runtime, plan_id: str, task_id: str
) -> AsyncIterator[str]:
    cursor = 0
    while not await request.is_disconnected():
        events = await asyncio.to_thread(runtime.repository.list_events, plan_id, cursor)
        for event in events:
            cursor = event.sequence
            if event.task_id == task_id and event.event_type == "task_log":
                message = str(event.payload["message"])
                log_data = "\n".join(f"data: {line}" for line in message.splitlines())
                yield f"event: log\n{log_data}\n\n"
        plan = await asyncio.to_thread(runtime.repository.get_plan, plan_id)
        node = next(node for node in plan.nodes if node.id == task_id)
        if len(events) < 200 and node.status in {
            Status.COMPLETED,
            Status.FAILED,
            Status.CANCELED,
            Status.BLOCKED,
        }:
            # 节点可能在事件读取后才完成；先排空该终态已经提交的日志，再发送最终结果。
            if await asyncio.to_thread(runtime.repository.list_events, plan_id, cursor):
                continue
            if node.status == Status.COMPLETED:
                data = task_result_payload(node)
                yield f"event: result\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            else:
                yield f"event: error\ndata: {json.dumps(node.error, ensure_ascii=False)}\n\n"
            return
        yield ": heartbeat\n\n"
        await asyncio.sleep(0.25)


@router.post("/execute")
async def direct_execution(
    payload: DirectExecutionRequest,
    request: Request,
    response: Response,
    runtime: RuntimeDep,
    identity: IdentityDep,
) -> Response:
    selected = await LegacyExecutionService(
        runtime.settings, runtime.repository, runtime.plans, runtime.uploads, runtime.files
    ).prepare(payload, identity)
    plan_id, task_id = selected.plan.id, selected.task_id
    if selected.plan.status == Status.AWAITING_APPROVAL:
        return _identity_response(
            JSONResponse(
                status_code=202,
                content={
                    "approval_required": True,
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "plan_graph": plan_payload(selected.plan),
                },
            ),
            response,
        )
    return _identity_response(
        StreamingResponse(
            legacy_events(request, runtime, plan_id)
            if selected.plan.legacy and task_id == selected.plan.legacy.native_task_id
            else direct_events(request, runtime, plan_id, task_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-OpenResearch-Plan-Id": plan_id,
                "X-OpenResearch-Task-Id": task_id,
            },
        ),
        response,
    )


def _identity_response(result: Response, identity: Response) -> Response:
    # 返回 Response 对象时框架不自动合并依赖中的 Cookie；旧入口首次访问也须保留主体身份。
    result.raw_headers.extend(
        header for header in identity.raw_headers if header[0].lower() == b"set-cookie"
    )
    return result
