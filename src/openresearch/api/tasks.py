import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from openresearch.api.dependencies import IdentityDep, Runtime, RuntimeDep
from openresearch.models.runtime import ContractModel, Status

router = APIRouter(prefix="/api", tags=["tasks"])


class ReassignRequest(ContractModel):
    assigned_to: str


class DirectExecutionRequest(ContractModel):
    task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    plan_id: str | None = None
    task_name: str = ""
    task_type: str = ""
    task_description: str = ""
    assigned_to: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)


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
            if node.status == Status.COMPLETED:
                data = {
                    "result": node.result,
                    "code": node.code,
                    "structured_data": node.structured_data,
                }
                yield f"event: result\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            else:
                yield f"event: error\ndata: {json.dumps(node.error, ensure_ascii=False)}\n\n"
            return
        yield ": heartbeat\n\n"
        await asyncio.sleep(0.25)


@router.post("/execute")
async def direct_execution(
    payload: DirectExecutionRequest, request: Request, runtime: RuntimeDep, identity: IdentityDep
) -> StreamingResponse:
    plan_id: str
    if payload.plan_id is None:
        plan_id = await asyncio.to_thread(
            runtime.repository.tasks.find_plan, payload.task_id, identity.user_id
        )
    else:
        plan_id = payload.plan_id
    # 客户端只选择已保存节点；类型、权限和执行参数从持久化图读取，禁止绕过契约。
    await asyncio.to_thread(
        runtime.repository.tasks.queue, plan_id, payload.task_id, identity.user_id
    )
    return StreamingResponse(
        direct_events(request, runtime, plan_id, payload.task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
