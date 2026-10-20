from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.compute import ReproductionRequest
from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import Task, TaskResult
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.research.reproduction_mode import decide_mode
from openresearch.research.runtime_selection import runtime_python
from openresearch.sandbox.session import SandboxSessions


class ComputeAgent:
    def __init__(self, settings: Settings, sessions: SandboxSessions) -> None:
        self.settings, self.sessions = settings, sessions

    async def execute(self, task: Task, lease: ExecutionLease) -> TaskResult:
        if "runtime.probe" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to probe compute resources")
        request = ReproductionRequest.model_validate(task.inputs["reproduction"])
        runtime = (
            RuntimeBinding.model_validate(task.inputs["runtime"])
            if task.inputs.get("runtime")
            else None
        )
        if runtime and "runtime.select" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to select a compute runtime")
        expected_python = runtime_python(self.settings, runtime)
        async with self.sessions.open(lease, runtime.profile if runtime else None) as session:
            probe = await session.adapter.resources(session.id)
        if not probe.python.startswith(expected_python + "."):
            raise ContractError("resource probe interpreter differs from the selected runtime")
        decision = decide_mode(self.settings, request, probe)
        decision.runtime = runtime
        return TaskResult(
            success=decision.execution_allowed,
            error=""
            if decision.execution_allowed
            else "资源不足，未执行完整复现或请求的 GPU 程序：" + "; ".join(decision.reasons),
            result="实际资源核对完成。"
            if decision.execution_allowed
            else "资源条件不满足；实际探测回执已保存，未运行实验。",
            structured_data=decision.model_dump_json(),
            outputs={"reproduction_decision": decision.model_dump_json()},
        )
