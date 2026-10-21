import re

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Task, TaskContract
from openresearch.research.gap import CONTEXT_KEYS, GAP_OUTPUTS

GAP_PREFIX = "result_gap__"
COMPARISON_OUTPUTS = [
    "gap_source_snapshot",
    "audit_report",
    "comparison_report",
    "gap_before_claim_evidence_graph",
    "gap_before_claim_verification_report",
]


def requests_gap_repair(intent: str) -> bool:
    text = re.sub(r"```.*?```|`[^`]*`", "", intent, flags=re.S)
    if re.search(
        r"(?:不要|无需|不用|禁止|不需要|不允许|不|without|do not|don't|no)\s*"
        r"(?:自动|再|进行|做)?\s*(?:结果(?:差距)?|差距|result\s+gap)?\s*"
        r"(?:修复|重跑|repair|fix|rerun)",
        text,
        re.I,
    ):
        return False
    return bool(
        re.search(
            r"fix_and_rerun|(?:修复|排查|重跑)[^。；\n]{0,24}(?:差距|结果|不一致)|"
            r"(?:差距|结果|不一致)[^。；\n]{0,24}(?:修复|排查|重跑)|"
            r"(?:fix|repair|debug)[^.;\n]{0,32}(?:result|gap|mismatch)|"
            r"(?:result gap|mismatch)[^.;\n]{0,32}(?:fix|repair|rerun)",
            text,
            re.I,
        )
    )


def append_gap_tasks(
    nodes: list[Task], source: Task, spec: ResearchSpec, settings: Settings
) -> Task:
    if spec.max_repairs == 0 or "research.patch" not in source.contract.allowed_tools:
        raise ContractError("result-gap workflow requires source repair permission and budget")
    comparison = Task(
        name="比较实际结果与冻结规则",
        type="result_audit",
        assigned_to="data_agent",
        inputs={"native_gap_comparison": True, "source_task_id": source.id},
        dependencies=[source.id],
        required_artifacts=list(source.output_artifacts),
        output_artifacts=COMPARISON_OUTPUTS,
        timeout_seconds=settings.task_timeout_seconds,
        contract=TaskContract(
            input_artifacts=list(source.output_artifacts),
            output_artifacts=COMPARISON_OUTPUTS,
            allowed_tools=["results.audit", "claims.assess"],
        ),
    )
    context_keys = sorted({key for node in nodes for key in node.output_artifacts} & CONTEXT_KEYS)
    optional = GAP_OUTPUTS[4:] + (
        ["benchmark_preflight_report", "benchmark_preflight_patch"] if spec.benchmark else []
    )
    outputs = [
        GAP_PREFIX + key
        for key in [*context_keys, "research_spec", "environment_lock", *GAP_OUTPUTS]
    ]
    if spec.benchmark:
        outputs += [
            GAP_PREFIX + key for key in ["benchmark_preflight_report", "benchmark_preflight_patch"]
        ]
    inputs = [*source.output_artifacts, *COMPARISON_OUTPUTS]
    repair = Task(
        name="按原规则修复结果差距并重跑",
        type="research_gap_repair",
        assigned_to="coder_agent",
        artifact_scope="result_gap",
        inputs={"source_task_id": source.id, "research": spec.model_dump()},
        description="根据原实际比较和冻结验收规则诊断结果差距；有据补丁才实际重跑，不改数据或检查。",
        dependencies=[source.id, comparison.id],
        required_artifacts=inputs,
        output_artifacts=outputs,
        timeout_seconds=settings.task_timeout_seconds,
        contract=TaskContract(
            input_artifacts=inputs,
            output_artifacts=outputs,
            optional_output_artifacts=[GAP_PREFIX + key for key in optional],
            allowed_tools=list(
                dict.fromkeys(
                    [*source.contract.allowed_tools, "results.audit", "results.gap_repair"]
                )
            ),
        ),
    )
    nodes.extend([comparison, repair])
    return repair
