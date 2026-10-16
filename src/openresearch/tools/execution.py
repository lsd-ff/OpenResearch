import json

from openresearch.errors import ContractError, SandboxError
from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import ExecutionResult, Task, TaskResult
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.session import SandboxSessions
from openresearch.storage.repository import Repository


class ExecutionTool:
    def __init__(self, repository: Repository, sandbox: Sandbox) -> None:
        self.sessions = SandboxSessions(repository, sandbox)

    async def execute(self, task: Task, lease: ExecutionLease, code: str) -> TaskResult:
        """按任务权限执行 Python，容器生命周期由统一会话管理器负责。"""
        if "sandbox.python" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to execute Python")
        async with self.sessions.open(lease) as session:
            result = await session.execute(code)
        return self._result(result)

    @staticmethod
    def _result(result: ExecutionResult) -> TaskResult:
        if result.exit_code != 0:
            raise SandboxError(
                f"Python exited with code {result.exit_code}: {result.stderr[:2000]}"
            )
        summary = json.dumps(
            {
                "exit_code": result.exit_code,
                "stdout_bytes": len(result.stdout.encode()),
                "stderr_bytes": len(result.stderr.encode()),
            }
        )
        return TaskResult(
            result=result.stdout[:4096],
            structured_data=summary,
            outputs={"run_metrics": result.model_dump_json()},
        )
