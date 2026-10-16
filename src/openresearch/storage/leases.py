from openresearch.errors import StaleExecutionError
from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import Plan, Status, Task, utcnow


def current_task(plan: Plan, lease: ExecutionLease) -> Task:
    """在写事务内校验租约，防止取消、恢复后的旧执行覆盖当前状态。"""
    node = next((node for node in plan.nodes if node.id == lease.task_id), None)
    if (
        plan.id != lease.plan_id
        or node is None
        or plan.status != Status.IN_PROGRESS
        or node.status != Status.IN_PROGRESS
        or node.execution_id != lease.execution_id
        or node.lease_owner != lease.worker_id
        or node.lease_expires_at is None
        or node.lease_expires_at <= utcnow()
    ):
        raise StaleExecutionError("execution lease is no longer current")
    return node


def duration_exhausted(plan: Plan) -> bool:
    if plan.usage.started_at is None:
        return False
    return (utcnow() - plan.usage.started_at).total_seconds() >= plan.budget.max_duration_seconds
