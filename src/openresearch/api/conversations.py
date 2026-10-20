import asyncio
from typing import Any

from fastapi import APIRouter

from openresearch.api.dependencies import IdentityDep, RuntimeDep
from openresearch.api.payloads import message_payload, papers_payload
from openresearch.api.schemas import ChatRequest, MessageRequest
from openresearch.models.submissions import PlanSubmission

router = APIRouter(prefix="/api", tags=["conversations"])


@router.get("/session")
def get_session(runtime: RuntimeDep, identity: IdentityDep) -> dict[str, Any]:
    """会话以所有者和会话 ID 共同隔离；浏览器重启不会丢失上下文。"""
    return runtime.repository.sessions.get(identity).model_dump(mode="json")


@router.post("/chat")
async def chat(payload: ChatRequest, runtime: RuntimeDep, identity: IdentityDep) -> dict[str, Any]:
    """使用当前会话历史和已解析来源回答，引用通过页号及文本校验。"""
    reply, revision = await runtime.chat.answer(identity, payload.message)
    return {**reply.model_dump(), "context_revision": revision}


@router.post("/messages")
async def send_message(
    payload: MessageRequest, runtime: RuntimeDep, identity: IdentityDep
) -> dict[str, Any]:
    result = await runtime.messages.send(
        PlanSubmission(intent=payload.message, identity=identity, attachments=payload.attachments)
    )
    papers = await asyncio.to_thread(papers_payload, result.plan, runtime.repository)
    return {**message_payload(result), "papers": papers}
