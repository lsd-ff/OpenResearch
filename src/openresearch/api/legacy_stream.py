import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request

from openresearch.api.dependencies import Runtime
from openresearch.models.legacy import LegacyEnvironmentState
from openresearch.models.runtime import Plan, Status, Task


def task_result_payload(node: Task) -> dict[str, Any]:
    return {
        "result": node.result,
        "code": node.code,
        "structured_data": node.structured_data,
        "image_base64": node.image_base64,
        "image_base_64": node.image_base64,
        "outputs": {
            key: value
            for key, value in {
                "result": node.result,
                "generated_code": node.code,
                "structured_data": node.structured_data,
                "image_base64": node.image_base64,
            }.items()
            if value
        },
    }


async def legacy_result(runtime: Runtime, plan: Plan) -> dict[str, Any]:
    assert plan.legacy is not None
    node = next(node for node in plan.nodes if node.id == plan.legacy.native_task_id)
    outputs: dict[str, Any] = {}
    for key, artifact in plan.artifacts.items():
        if artifact.producer_task_id == node.id:
            outputs[key] = await asyncio.to_thread(runtime.files.read, artifact)
    outputs.update(task_result_payload(node)["outputs"])
    result = node.result
    if "legacy_environment_state" in outputs:
        state = LegacyEnvironmentState.model_validate_json(outputs["legacy_environment_state"])
        outputs["dependency_spec"] = json.dumps(
            state.requests if state.phase == "probe" else state.spec.requirements,
            ensure_ascii=False,
        )
        outputs["runtime_session"] = outputs["runtime_env"] = "openresearch:" + plan.id
        if state.installation:
            outputs["prepared_runtime"] = "openresearch:" + plan.id
            outputs["dependency_install_report"] = state.installation.model_dump_json()
        if plan.legacy.task_type == "resolve_dependencies":
            result = outputs["dependency_spec"]
    if result:
        outputs["result"] = result
    return {
        "result": result,
        "code": node.code,
        "structured_data": node.structured_data,
        "image_base64": node.image_base64,
        "image_base_64": node.image_base64,
        "outputs": outputs,
        "plan_id": plan.id,
        "task_id": node.id,
        "client_task_id": plan.legacy.task_id,
    }


async def legacy_events(request: Request, runtime: Runtime, plan_id: str) -> AsyncIterator[str]:
    cursor = 0
    while not await request.is_disconnected():
        events = await asyncio.to_thread(runtime.repository.list_events, plan_id, cursor)
        for event in events:
            cursor = event.sequence
            if event.event_type == "task_log":
                data = "\n".join(
                    "data: " + line for line in str(event.payload["message"]).splitlines()
                )
                yield f"event: log\n{data}\n\n"
        plan = await asyncio.to_thread(runtime.repository.get_plan, plan_id)
        if plan.status in {Status.COMPLETED, Status.FAILED, Status.CANCELED}:
            # 与原整图/单节点流相同，终态快照之后再确认尾部日志已排空。
            if await asyncio.to_thread(runtime.repository.list_events, plan_id, cursor):
                continue
            if plan.status == Status.COMPLETED:
                result = await legacy_result(runtime, plan)
                yield "event: result\ndata: " + json.dumps(result, ensure_ascii=False) + "\n\n"
            else:
                error = next(
                    (node.error for node in plan.nodes if node.error),
                    "managed legacy execution stopped",
                )
                yield "event: error\ndata: " + json.dumps(error, ensure_ascii=False) + "\n\n"
            return
        yield ": heartbeat\n\n"
        await asyncio.sleep(0.25)
