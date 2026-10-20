from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.blueprint import NodeBlueprint, PlanBlueprint
from openresearch.models.routing import validate_role
from openresearch.models.runtime import Edge, Plan, PlanningInfo, Task, TaskContract

EXTRA_TASKS: dict[str, tuple[str, list[str]]] = {
    "chat_answer": ("chat.answer", ["chat_answer"]),
    "source_discover": ("sources.search", ["discovered_sources"]),
    "data_analyze": ("data.profile", ["data_profile", "data_chart"]),
}


def extra_task(node: NodeBlueprint, plan: Plan, settings: Settings) -> Task:
    if node.type not in EXTRA_TASKS:
        raise ContractError("planner requested a task outside the capability catalog")
    tool, outputs = EXTRA_TASKS[node.type]
    task = Task(
        name=node.name,
        type=node.type,
        assigned_to=node.assigned_to,
        description=node.description,
        timeout_seconds=settings.task_timeout_seconds,
        output_artifacts=outputs,
        contract=TaskContract(allowed_tools=[tool], output_artifacts=outputs),
    )
    if node.type == "source_discover":
        if not node.query:
            raise ContractError("source discovery requires a bounded search query")
        task.inputs = {"kind": node.search_kind, "query": node.query, "limit": 5}
    if node.type == "data_analyze":
        if not node.upload_id or node.upload_id not in plan.attachments:
            raise ContractError("planner may only analyze the declared attachments")
        task.inputs = {"upload_id": node.upload_id}
    return task


def bind_stages(blueprint: PlanBlueprint, template: Plan) -> dict[str, Task]:
    originals = {task.id: task for task in template.nodes}
    bound: dict[str, Task] = {}
    used: set[str] = set()
    for node in blueprint.nodes:
        matches = [task for task in template.nodes if task.type == node.type]
        original = originals.get(node.template_task_id or "")
        if node.template_task_id and (original is None or original.type != node.type):
            raise ContractError("planner referenced an unknown or incompatible stage identity")
        if original is None and len(matches) == 1:
            original = matches[0]
        elif original is None and matches:
            raise ContractError("parallel branch stages require template_task_id")
        if original is None:
            continue
        if original.id in used:
            raise ContractError("planner reused a required workflow stage")
        bound[node.ref] = original
        used.add(original.id)
    if set(originals) - used:
        raise ContractError("planner omitted a required workflow stage")
    return bound


def materialize(blueprint: PlanBlueprint, template: Plan, settings: Settings) -> Plan:
    refs = [node.ref for node in blueprint.nodes]
    if len(set(refs)) != len(refs):
        raise ContractError("planner references must be unique")
    originals = bind_stages(blueprint, template)
    if len(refs) > template.budget.max_task_attempts:
        raise ContractError("planner graph exceeds the configured attempt budget")
    tasks: dict[str, Task] = {}
    for node in blueprint.nodes:
        validate_role(node.type, node.assigned_to)
        original = originals.get(node.ref)
        # 基础节点的源码、资产、工具和产物契约由服务器保存，模型不能覆盖。
        task = (
            Task.model_validate(original.model_dump())
            if original
            else extra_task(node, template, settings)
        )
        task.name, task.description, task.assigned_to = (
            node.name,
            node.description,
            node.assigned_to,
        )
        task.priority, task.parallelizable = node.priority, node.parallelizable
        tasks[node.ref] = task
    for node in blueprint.nodes:
        if any(ref not in tasks for ref in node.dependencies):
            raise ContractError("planner referenced an unknown dependency")
        task = tasks[node.ref]
        task.dependencies = [tasks[ref].id for ref in node.dependencies]
        if node.type == "chat_answer":
            task.required_artifacts = [
                key for ref in node.dependencies for key in tasks[ref].output_artifacts
            ]
            task.contract.input_artifacts = list(task.required_artifacts)
    payload = template.model_dump(by_alias=True)
    payload["nodes"] = list(tasks.values())
    payload["edges"] = [
        Edge(**{"from": parent, "to": task.id})
        for task in tasks.values()
        for parent in task.dependencies
    ]
    payload["planning"] = PlanningInfo(strategy="llm", reason=blueprint.strategy)
    return Plan.model_validate(payload)
