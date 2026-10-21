import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request

from openresearch.api.dependencies import Runtime
from openresearch.api.legacy_stage_outputs import stage_outputs
from openresearch.models.audit import ReviewSnapshot
from openresearch.models.graph import ancestor_map
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


async def legacy_result(
    runtime: Runtime, plan: Plan, task_id: str | None = None, operation: str | None = None
) -> dict[str, Any]:
    if task_id is None:
        assert plan.legacy is not None
        task_id = plan.legacy.native_task_id
    node = next(node for node in plan.nodes if node.id == task_id)
    outputs: dict[str, Any] = {}
    for key, artifact in plan.artifacts.items():
        if artifact.producer_task_id == node.id:
            outputs[key] = await asyncio.to_thread(runtime.files.read, artifact)
    outputs.update(task_result_payload(node)["outputs"])
    outputs.update(await asyncio.to_thread(stage_outputs, runtime, plan, node))
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
        if operation == "resolve_dependencies" or (
            plan.legacy
            and node.id == plan.legacy.native_task_id
            and plan.legacy.task_type == "resolve_dependencies"
        ):
            result = outputs["dependency_spec"]
    if result:
        outputs["result"] = result
    return {
        "result": result,
        "code": node.code or outputs.get("generated_code", ""),
        "structured_data": node.structured_data,
        "image_base64": node.image_base64,
        "image_base_64": node.image_base64,
        "outputs": outputs,
        "plan_id": plan.id,
        "task_id": node.id,
        "client_task_id": plan.legacy.task_id if plan.legacy else "",
        "workflow_ref": "openresearch:" + plan.id,
        **(
            {
                "source_workflow_ref": "openresearch:"
                + ReviewSnapshot.model_validate(node.inputs["snapshot"]).source_plan_id
            }
            if node.type in {"result_audit", "research_gap_repair"}
            else {}
        ),
    }


async def legacy_events(
    request: Request,
    runtime: Runtime,
    plan_id: str,
    task_id: str | None = None,
    operation: str | None = None,
) -> AsyncIterator[str]:
    cursor = 0
    initial = await asyncio.to_thread(runtime.repository.get_plan, plan_id)
    if task_id is None:
        assert initial.legacy is not None
        task_id = initial.legacy.native_task_id
    scope = ancestor_map({node.id: node for node in initial.nodes})[task_id] | {task_id}
    while not await request.is_disconnected():
        events = await asyncio.to_thread(runtime.repository.list_events, plan_id, cursor)
        for event in events:
            cursor = event.sequence
            if event.event_type == "task_log" and event.task_id in scope:
                data = "\n".join(
                    "data: " + line for line in str(event.payload["message"]).splitlines()
                )
                yield f"event: log\n{data}\n\n"
        plan = await asyncio.to_thread(runtime.repository.get_plan, plan_id)
        target = next(node for node in plan.nodes if node.id == task_id)
        if target.status in {Status.COMPLETED, Status.FAILED, Status.CANCELED, Status.BLOCKED}:
            # 与原整图/单节点流相同，终态快照之后再确认尾部日志已排空。
            if await asyncio.to_thread(runtime.repository.list_events, plan_id, cursor):
                continue
            if target.status == Status.COMPLETED:
                result = await legacy_result(runtime, plan, task_id, operation)
                yield "event: result\ndata: " + json.dumps(result, ensure_ascii=False) + "\n\n"
            else:
                error = next(
                    (node.error for node in plan.nodes if node.id in scope and node.error),
                    "managed legacy execution stopped",
                )
                yield "event: error\ndata: " + json.dumps(error, ensure_ascii=False) + "\n\n"
            return
        yield ": heartbeat\n\n"
        await asyncio.sleep(0.25)
