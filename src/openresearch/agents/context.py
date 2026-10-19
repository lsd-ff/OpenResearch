from dataclasses import dataclass
from typing import Protocol

from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import Plan, Task, TaskResult


@dataclass(frozen=True)
class AgentContext:
    plan: Plan
    task: Task
    lease: ExecutionLease


class TaskAgent(Protocol):
    async def execute(self, context: AgentContext) -> TaskResult: ...
