import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from openresearch.api.dependencies import IdentityDep, Runtime, RuntimeDep
from openresearch.models.runtime import Status

router = APIRouter(prefix="/api/plans", tags=["events", "artifacts"])
TERMINAL = {Status.COMPLETED, Status.FAILED, Status.CANCELED}


@router.get("/{plan_id}/events")
def list_events(
    plan_id: str,
    runtime: RuntimeDep,
    identity: IdentityDep,
    after: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """按持久化序号读取事件；使用 next_cursor 继续读取。"""
    runtime.repository.get_plan(plan_id, identity.user_id)
    history = runtime.repository.list_events(plan_id, after)
    return {
        "events": [item.model_dump(mode="json") for item in history],
        "next_cursor": history[-1].sequence if history else after,
    }


def event_cursor(request: Request, after: int) -> int:
    try:
        previous_cursor = int(request.headers.get("Last-Event-ID", "0"))
    except ValueError as exc:
        raise HTTPException(400, "invalid Last-Event-ID") from exc
    if previous_cursor < 0:
        raise HTTPException(400, "invalid Last-Event-ID")
    return max(after, previous_cursor)


async def generate_events(
    request: Request, runtime: Runtime, plan_id: str, cursor: int
) -> AsyncIterator[str]:
    last_heartbeat = time.monotonic()
    while not await request.is_disconnected():
        history = await asyncio.to_thread(runtime.repository.list_events, plan_id, cursor)
        for item in history:
            cursor = item.sequence
            value = json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
            yield f"id: {cursor}\nevent: plan_event\ndata: {value}\n\n"
        plan = await asyncio.to_thread(runtime.repository.get_plan, plan_id)
        # 先排空持久化事件再关闭终态流，断线重连也能收到完整终态。
        if len(history) < 200 and plan.status in TERMINAL:
            return
        if time.monotonic() - last_heartbeat >= 10:
            yield ": heartbeat\n\n"
            last_heartbeat = time.monotonic()
        await asyncio.sleep(0.25)


@router.get("/{plan_id}/stream")
async def stream_events(
    plan_id: str,
    request: Request,
    runtime: RuntimeDep,
    identity: IdentityDep,
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """SSE 使用 id 和 Last-Event-ID 提供可恢复的事件订阅。"""
    await asyncio.to_thread(runtime.repository.get_plan, plan_id, identity.user_id)
    return StreamingResponse(
        generate_events(request, runtime, plan_id, event_cursor(request, after)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{plan_id}/artifacts/{artifact_id}/content")
async def artifact_content(
    plan_id: str, artifact_id: str, runtime: RuntimeDep, identity: IdentityDep
) -> Response:
    """校验归属和内容摘要后读取完整产物。"""
    artifact = await asyncio.to_thread(
        runtime.repository.get_artifact, plan_id, artifact_id, identity.user_id
    )
    content = await asyncio.to_thread(runtime.files.read, artifact)
    return Response(content, media_type="text/plain", headers={"ETag": f'"{artifact.sha256}"'})
