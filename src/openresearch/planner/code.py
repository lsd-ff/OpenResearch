import re

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.runtime import Approval, Budget, Edge, Plan, Status, Task, TaskContract

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
    generate, execute = ["code.generate"], ["sandbox.python"]
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
    approval = Approval(
        required=settings.require_approval,
        status="pending" if settings.require_approval else "not_required",
    )
    return Plan(
        user_intent=intent,
        owner_id=owner_id,
        session_id=session_id,
        nodes=[generate, execute],
        edges=[Edge(**{"from": generate.id, "to": execute.id})],
        approval=approval,
        status=Status.AWAITING_APPROVAL if approval.required else Status.PENDING,
        budget=Budget(
            max_task_attempts=settings.max_task_attempts,
            max_duration_seconds=settings.plan_timeout_seconds,
        ),
    )
