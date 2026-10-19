import json

from openresearch.errors import ContractError, SandboxError
from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import ExecutionResult, Task, TaskResult
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.collect import collect_files
from openresearch.sandbox.session import SandboxSessions
from openresearch.storage.repository import Repository


class ExecutionTool:
    def __init__(
        self, repository: Repository, sandbox: Sandbox, max_output_bytes: int = 1024 * 1024
    ) -> None:
        self.sessions = SandboxSessions(repository, sandbox)
        self.max_output_bytes = max_output_bytes

    async def execute(self, task: Task, lease: ExecutionLease, code: str) -> TaskResult:
        """按任务权限执行 Python，容器生命周期由统一会话管理器负责。"""
        if "sandbox.python" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to execute Python")
        async with self.sessions.open(lease) as session:
            result = await session.execute(code)
            bundle = (
                await collect_files(session, self.max_output_bytes)
                if result.exit_code == 0 and "execution_files" in task.output_artifacts
                else None
            )
        completion = self._result(result)
        if bundle is not None:
            completion.outputs["execution_files"] = bundle.model_dump_json()
            completion.image_base64 = bundle.preview()
        return completion

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
