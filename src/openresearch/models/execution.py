from dataclasses import dataclass, field

from openresearch.errors import ContractError
from openresearch.models.runtime import Artifact, Plan, Task, TaskResult


@dataclass(frozen=True)
class ExecutionLease:
    """一次执行的身份；旧执行不能以新任务状态提交结果。"""

    plan_id: str
    task_id: str
    execution_id: str
    worker_id: str

    @classmethod
    def for_task(cls, plan: Plan, task: Task, worker_id: str) -> "ExecutionLease":
        if task.execution_id is None:
            raise ContractError("claimed task is missing its execution ID")
        return cls(plan.id, task.id, task.execution_id, worker_id)


@dataclass(frozen=True)
class TaskCompletion:
    """提交结果与产物，或提交失败原因；状态转换仍由存储层校验。"""

    result: TaskResult | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    error: str = ""
