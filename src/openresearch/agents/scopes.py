import re

from openresearch.agents.context import AgentContext
from openresearch.errors import ContractError
from openresearch.models.runtime import Plan, Task, TaskResult


def scoped_context(context: AgentContext) -> AgentContext:
    if context.task.artifact_scope is None:
        return context
    plan, task = scoped_plan_task(context.plan, context.task)
    return AgentContext(plan, task, context.lease)


def scoped_plan_task(plan: Plan, task: Task) -> tuple[Plan, Task]:
    if task.artifact_scope is None:
        return plan, task
    if plan.intent_type != "Framework_Comparison":
        raise ContractError("artifact scopes are restricted to comparison branches")
    prefix = task.artifact_scope + "__"

    def local(key: str) -> str:
        if key.startswith(prefix):
            return key[len(prefix) :]
        if re.match(r"^framework_[1-9][0-9]*__", key):
            raise ContractError("a framework branch cannot consume another branch's private output")
        return key

    if any(not key.startswith(prefix) for key in task.output_artifacts):
        raise ContractError("every framework branch output must use its own namespace")
    inputs = [local(key) for key in task.required_artifacts]
    outputs = [local(key) for key in task.output_artifacts]
    contract = task.contract.model_copy(
        update={"input_artifacts": inputs, "output_artifacts": outputs}
    )
    projected_task = task.model_copy(
        update={
            "required_artifacts": inputs,
            "output_artifacts": outputs,
            "contract": contract,
            "artifact_scope": None,
        }
    )
    # 仅在执行器边界提供分支视图；持久图、租约与最终产物仍保留完整身份。
    artifacts = {
        local(key): artifact
        for key, artifact in plan.artifacts.items()
        if key.startswith(prefix) or not re.match(r"^framework_[1-9][0-9]*__", key)
    }
    return plan.model_copy(update={"artifacts": artifacts}), projected_task


def scoped_result(context: AgentContext, result: TaskResult) -> TaskResult:
    if context.task.artifact_scope is not None:
        prefix = context.task.artifact_scope + "__"
        result.outputs = {prefix + key: value for key, value in result.outputs.items()}
    return result
