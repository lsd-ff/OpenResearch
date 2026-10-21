from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openresearch.models.runtime import Plan, Task


def artifact_producers(nodes: dict[str, "Task"]) -> dict[str, str]:
    producers: dict[str, str] = {}
    for node in nodes.values():
        optional = set(node.contract.optional_output_artifacts)
        canonical_optional = {key.removeprefix("result_gap__") for key in optional}
        if optional and (
            node.type != "research_gap_repair"
            or node.artifact_scope not in {None, "result_gap"}
            or not optional.issubset(node.output_artifacts)
            or canonical_optional
            - {
                "research_report",
                "benchmark_predictions",
                "final_patch",
                "claim_evidence_graph",
                "claim_verification_report",
                "benchmark_preflight_report",
                "benchmark_preflight_patch",
            }
        ):
            raise ValueError("optional execution receipts are restricted to result-gap tasks")
        if node.trigger_rule == "all_done" and (
            node.type not in {"framework_report", "chat_answer"}
            or node.assigned_to not in {"chat_agent", "data_agent"}
            or set(node.contract.allowed_tools) - {"chat.answer", "comparison.report"}
        ):
            raise ValueError("failure finalizers must be read-only report tasks")
        if set(node.contract.input_artifacts) != set(node.required_artifacts):
            raise ValueError("task input contract does not match required artifacts")
        if set(node.contract.output_artifacts) != set(node.output_artifacts):
            raise ValueError("task output contract does not match output artifacts")
        if len(set(node.dependencies)) != len(node.dependencies):
            raise ValueError("task dependencies must be unique")
        if any(dependency not in nodes for dependency in node.dependencies):
            raise ValueError("unknown task dependency")
        for key in node.output_artifacts:
            if key in producers:
                raise ValueError("artifact keys must have one producer per plan")
            producers[key] = node.id
    return producers


def ancestor_map(nodes: dict[str, "Task"]) -> dict[str, frozenset[str]]:
    visiting: set[str] = set()

    @cache
    def ancestors(task_id: str) -> frozenset[str]:
        if task_id in visiting:
            raise ValueError("task graph contains a cycle")
        visiting.add(task_id)
        upstream: set[str] = set()
        for dependency in nodes[task_id].dependencies:
            upstream.add(dependency)
            upstream.update(ancestors(dependency))
        visiting.remove(task_id)
        return frozenset(upstream)

    # 缓存每个节点的祖先集合，菱形依赖图不会重复递归展开。
    return {task_id: ancestors(task_id) for task_id in nodes}


def validate_graph(plan: "Plan") -> None:
    if not plan.nodes or len(plan.nodes) > 64:
        raise ValueError("a plan must contain between 1 and 64 tasks")
    nodes = {node.id: node for node in plan.nodes}
    if len(nodes) != len(plan.nodes):
        raise ValueError("task IDs must be unique")
    if plan.execution_scope is not None and (
        not plan.execution_scope or set(plan.execution_scope) - set(nodes)
    ):
        raise ValueError("execution scope must reference existing tasks")
    producers = artifact_producers(nodes)
    ancestors = ancestor_map(nodes)
    for node in nodes.values():
        if node.artifact_scope == "result_gap" and (
            node.type != "research_gap_repair"
            or plan.intent_type not in {"Paper_Reproduction", "Benchmark"}
            or any(not key.startswith("result_gap__") for key in node.output_artifacts)
        ):
            raise ValueError("result-gap namespaces require a native scientific repair stage")
        if node.trigger_rule == "all_done" and (set(nodes) - {node.id}) - ancestors[node.id]:
            raise ValueError("a final report must wait for every other task")
        if any(producers.get(key) not in ancestors[node.id] for key in node.required_artifacts):
            raise ValueError("required artifact must be produced by an ancestor")
    for edge in plan.edges:
        if edge.from_ not in nodes or edge.to not in nodes:
            raise ValueError("edge references an unknown task")
        if edge.type not in {"control", "data"}:
            raise ValueError("unknown edge type")
