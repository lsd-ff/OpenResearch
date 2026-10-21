import json

from openresearch.errors import ContractError
from openresearch.models.chat import ExecutionSummary
from openresearch.models.gap import GapDebugReport
from openresearch.models.research import BenchmarkPreflightReport, ResearchReport, ResearchRun
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.storage.files import ArtifactStore

STATUS_NAMES = {
    "not_justified": "没有有据补丁，未重跑",
    "insufficient_evidence": "证据不足，未重跑",
    "rejected": "补丁被拒绝",
    "rerun_completed": "程序重跑完成",
    "rerun_failed": "重跑失败",
}
TASK_STATUS_NAMES = {
    "completed": "已完成",
    "failed": "失败",
    "blocked": "未执行",
    "canceled": "已取消",
}


def report_section(label: str, report: ResearchReport) -> list[str]:
    lines = [
        "",
        f"{label}：程序{'完成' if report.success else '失败'}。",
        "实际指标："
        + json.dumps({run.variant: run.metrics for run in report.runs}, ensure_ascii=False),
    ]
    if report.preflight:
        preflight = report.preflight
        lines.append(
            f"独立预检：{preflight.status}；{len(preflight.sample_ids)} 条样本，"
            f"实际运行 {len(preflight.run.attempts)} 次。"
        )
        for attempt in preflight.run.attempts:
            if attempt.error:
                lines.append(
                    f"- 预检第 {attempt.number} 次：{attempt.error[-200:]}\n{attempt.stderr[-300:]}"
                )
    return [*lines, *run_section(report.runs[0])]


def run_section(baseline: ResearchRun) -> list[str]:
    lines = []
    lines.append(
        f"该阶段实际运行 {len(baseline.attempts)} 次；"
        f"最后退出码 {baseline.attempts[-1].exit_code}。"
    )
    for attempt in baseline.attempts:
        if attempt.error:
            lines.append(f"- 第 {attempt.number} 次（{attempt.phase}）：{attempt.error[-200:]}")
    if stdout := baseline.attempts[-1].stdout[-1000:]:
        lines.extend(["实际程序 stdout（末尾）：", "```text", stdout.replace("```", "`` `"), "```"])
    if stderr := baseline.attempts[-1].stderr[-500:]:
        lines.extend(["实际程序 stderr（末尾）：", "```text", stderr.replace("```", "`` `"), "```"])
    return lines


def native_gap_summary(plan: Plan, task: Task, files: ArtifactStore) -> TaskResult:
    source = next(
        (node for node in plan.nodes if node.id == task.inputs.get("source_task_id")), None
    )
    gap = next((node for node in plan.nodes if node.id == task.inputs.get("gap_task_id")), None)
    if source is None or gap is None or gap.type != "research_gap_repair":
        raise ContractError("native gap summary lost its frozen workflow identities")
    lines = [
        "# 科研执行与结果差距报告",
        "",
        f"原执行节点：`{source.id}`；修复节点：`{gap.id}`。",
        f"原执行状态：{TASK_STATUS_NAMES.get(source.status, source.status)}；"
        f"修复状态：{TASK_STATUS_NAMES.get(gap.status, gap.status)}。",
    ]
    if artifact := plan.artifacts.get("research_report"):
        lines.extend(
            report_section("原实验", ResearchReport.model_validate_json(files.read(artifact)))
        )
    elif artifact := plan.artifacts.get("benchmark_preflight_report"):
        preflight = BenchmarkPreflightReport.model_validate_json(files.read(artifact))
        lines.extend(
            [
                "",
                f"原独立预检：{preflight.status}；未产生正式运行回执。",
                *run_section(preflight.run),
            ]
        )
    if artifact := plan.artifacts.get("gap_before_claim_evidence_graph"):
        graph = json.loads(files.read(artifact))
        lines.extend(["", "原冻结规则判定：" + json.dumps(graph["summary"], ensure_ascii=False)])
    if artifact := plan.artifacts.get("result_gap__gap_debug_report"):
        debug = GapDebugReport.model_validate_json(files.read(artifact))
        lines.extend(
            [
                "",
                "差距修复：" + STATUS_NAMES[debug.status] + "。",
                f"进入预检：{debug.preflight_performed}；实际执行程序：{debug.execution_performed}。",
                "修复提议说明：" + debug.proposal.diagnosis[:1000] if debug.proposal else "",
            ]
        )
        if debug.error:
            lines.append("实际修复错误：" + debug.error[-1000:])
        if artifact := plan.artifacts.get("result_gap__research_report"):
            lines.extend(
                report_section(
                    "修复后的实际运行", ResearchReport.model_validate_json(files.read(artifact))
                )
            )
        if debug.acceptance_summary:
            lines.append(
                "修复后按原冻结规则判定："
                + json.dumps(debug.acceptance_summary, ensure_ascii=False)
            )
        lines.extend(["", debug.scope])
    else:
        lines.extend(["", "修复节点未留下运行回执；不能从缺失产物认定已重跑或已修复。"])
    failures = [node for node in plan.nodes if node.error and node.id != task.id]
    lines.extend(f"- {node.name[:80]}：{node.error[-250:]}" for node in failures[:8])
    lines.extend(
        [
            "",
            "程序完成与主张达标分别判定；未验证项仍未验证，原运行没有被新结果覆盖。",
            "完整代码、环境、预测、日志、逐次补丁和证据见对应执行节点的产物。",
        ]
    )
    body = "\n".join(lines)
    if len(body) > 15000:
        body = (
            body[:15000] + "\n\n摘要达到显示上限；完整逐次回执见节点产物。程序完成不代表主张达标。"
        )
    reply = ExecutionSummary(response=body)
    return TaskResult(result=reply.response, outputs={"chat_answer": reply.model_dump_json()})
