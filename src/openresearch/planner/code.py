import re

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.runtime import Approval, Budget, Edge, Plan, Status, Task, TaskContract
from openresearch.planner.compute import compute_task
from openresearch.research.compute import compute_runtime
from openresearch.research.reproduction_mode import request_from_intent
from openresearch.research.runtime_selection import runtime_tools

SMOKE_CODE = """import json
import platform

values = [1, 2, 3, 4, 5]
print(json.dumps({"status": "ok", "sum": sum(values), "python": platform.python_version()}))
"""


def code_permissions(intent: str) -> tuple[list[str], list[str]]:
    # 源码中的字符串只是待处理数据，不能替代代码块外的人类权限指令。
    intent = re.sub(r"```.*?```", "", intent, flags=re.S)
    disabled = bool(re.search(r"不要自动修复|不自动修复|禁止自动修复|不做任何修复", intent))
    immutable = disabled or bool(
        re.search(r"(?:不要|禁止|不允许)(?:修改|修复|改动)(?:这段|原始|任何)?(?:代码|程序)", intent)
    )
    no_install = disabled or bool(re.search(r"(?:不要|禁止|不允许)安装", intent))
    generate, execute = ["code.generate"], ["sandbox.python", "runtime.version"]
    if not immutable:
        generate.append("code.repair")
        execute.append("code.repair")
    if not no_install:
        execute.append("environment.lock")
    return generate, execute


def build_code_plan(
    intent: str, owner_id: str, session_id: str, settings: Settings, code: str | None = None
) -> Plan:
    """Explicit code and smoke experiments need no model API key."""
    fenced = re.search(r"```(?:python|py)?\s*\n(.*?)```", intent, re.DOTALL)
    if code is None and fenced:
        code = fenced.group(1)
    lower = intent.lower()
    if code is None and any(word in lower for word in ("论文", "复现", "benchmark", "reproduce")):
        raise ContractError(
            "论文复现和 Benchmark 需要真实材料，请在聊天中描述目标并提供论文、仓库或评测数据。"
        )
    if code is None and any(word in lower for word in ("smoke", "冒烟", "最小验证")):
        code = SMOKE_CODE
    if code is None and not settings.llm_api_key.get_secret_value():
        raise ContractError("provide Python code, request a smoke experiment, or configure an LLM")
    preparation_tools, execution_tools = code_permissions(intent)
    request = request_from_intent(intent)
    runtime = compute_runtime(settings, intent, request)
    preparation_tools.extend(runtime_tools(settings))
    execution_tools.extend(runtime_tools(settings))
    generate = Task(
        name="Prepare Python Code",
        type="code_generate",
        assigned_to="coder_agent",
        description=intent,
        inputs={"code": code} if code is not None else {},
        output_artifacts=["generated_code", "code_preparation_report"],
        timeout_seconds=settings.task_timeout_seconds,
        contract=TaskContract(
            output_artifacts=["generated_code", "code_preparation_report"],
            allowed_tools=preparation_tools,
        ),
    )
    execute = Task(
        name="Execute Python Experiment",
        type="code_execute",
        assigned_to="coder_agent",
        description=intent,
        dependencies=[generate.id],
        required_artifacts=["generated_code"],
        output_artifacts=[
            "run_metrics",
            "execution_files",
            "executed_code",
            "code_recovery_report",
        ],
        timeout_seconds=settings.task_timeout_seconds,
        contract=TaskContract(
            input_artifacts=["generated_code"],
            output_artifacts=[
                "run_metrics",
                "execution_files",
                "executed_code",
                "code_recovery_report",
            ],
            allowed_tools=execution_tools,
        ),
    )
    if runtime:
        generate.inputs["runtime"] = runtime.model_dump()
        execute.inputs["runtime"] = runtime.model_dump()
        for node in (generate, execute):
            if "runtime.select" not in node.contract.allowed_tools:
                node.contract.allowed_tools.append("runtime.select")
    nodes = [generate, execute]
    if request:
        compute = compute_task(settings, request, runtime)
        generate.dependencies = [compute.id]
        generate.required_artifacts = ["reproduction_decision"]
        generate.contract.input_artifacts = list(generate.required_artifacts)
        execute.dependencies.append(compute.id)
        execute.required_artifacts.append("reproduction_decision")
        execute.contract.input_artifacts = list(execute.required_artifacts)
        for node in nodes:
            node.inputs["reproduction"] = request.model_dump()
            node.contract.allowed_tools.append("runtime.probe")
        nodes.insert(0, compute)
        summary = Task(
            name="实际资源与执行报告",
            type="chat_answer",
            assigned_to="chat_agent",
            trigger_rule="all_done",
            dependencies=[node.id for node in nodes],
            output_artifacts=["chat_answer"],
            timeout_seconds=settings.task_timeout_seconds,
            contract=TaskContract(allowed_tools=["chat.answer"], output_artifacts=["chat_answer"]),
        )
        nodes.append(summary)
    approval = Approval(
        required=settings.require_approval,
        status="pending" if settings.require_approval else "not_required",
    )
    return Plan(
        user_intent=intent,
        owner_id=owner_id,
        session_id=session_id,
        nodes=nodes,
        edges=[
            Edge(**{"from": parent, "to": node.id})
            for node in nodes
            for parent in node.dependencies
        ],
        approval=approval,
        status=Status.AWAITING_APPROVAL if approval.required else Status.PENDING,
        budget=Budget(
            max_task_attempts=settings.max_task_attempts,
            max_duration_seconds=settings.plan_timeout_seconds,
        ),
    )
