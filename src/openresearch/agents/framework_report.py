import json
from typing import Any

from openresearch.errors import ContractError
from openresearch.models.comparison import (
    ComparisonSpec,
    FrameworkAnswerReport,
    FrameworkResearchReport,
    FrameworkSource,
)
from openresearch.models.research import ResearchReport
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.research.framework_protocol import read_protocol
from openresearch.storage.files import ArtifactStore


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def branch_row(plan: Plan, files: ArtifactStore, index: int, name: str) -> dict[str, Any]:
    scope = f"framework_{index + 1}"
    outcomes = [
        {"stage": node.type, "status": node.status.value, "error": node.error[:2000]}
        for node in plan.nodes
        if node.artifact_scope == scope
    ]
    row: dict[str, Any] = {
        "name": name,
        "tasks": outcomes,
        "complete": bool(outcomes) and all(item["status"] == "completed" for item in outcomes),
    }

    def read(key: str) -> str | None:
        artifact = plan.artifacts.get(scope + "__" + key)
        return files.read(artifact) if artifact else None

    source = read("framework_source")
    if source:
        row["source"] = FrameworkSource.model_validate_json(source).model_dump()
    if preparation := read("environment_preparation_report"):
        row["environment_preparation"] = json.loads(preparation)
    report_raw = read("research_report")
    if report_raw:
        report = ResearchReport.model_validate_json(report_raw)
        row.update(
            report=report.model_dump(),
            metrics=report.runs[0].metrics if report.runs else {},
            execution_seconds=report.runs[0].execution_seconds if report.runs else None,
            dataset_sha256=report.dataset_sha256,
        )
    answer = read("framework_answer_report")
    if answer:
        row["generation"] = FrameworkAnswerReport.model_validate_json(answer).model_dump()
    study = read("framework_research_report")
    if study:
        row["research"] = FrameworkResearchReport.model_validate_json(study).model_dump()
    return row


def framework_report(plan: Plan, task: Task, files: ArtifactStore) -> TaskResult:
    if "comparison.report" not in task.contract.allowed_tools or task.trigger_rule != "all_done":
        raise ContractError("comparison reports require an authorized read-only finalizer")
    spec = ComparisonSpec.model_validate(task.inputs["comparison"])
    rows = [
        branch_row(plan, files, index, target.name) for index, target in enumerate(spec.targets)
    ]
    protocol = read_protocol(plan, files) if "comparison_protocol" in plan.artifacts else None
    comparable = bool(protocol) and all(row["complete"] for row in rows)
    if protocol and protocol.mode == "benchmark":
        comparable = comparable and all(
            row.get("dataset_sha256") == protocol.dataset_sha256 for row in rows
        )
    lines = [
        "框架对比完成。" if comparable else "框架对比未完整通过，以下保留实际结果与失败。",
        "",
        protocol.scope if protocol else "共同协议未生成，不能认定比较有效。",
        "",
    ]
    if protocol:
        lines.extend(
            [
                f"数据来源：{protocol.origin}；样本数：{protocol.sample_count}。",
                f"数据 SHA-256：{protocol.dataset_sha256 or '无数据运行'}。",
                "",
            ]
        )
    for row in rows:
        lines.extend([f"**{cell(row['name'])}**", ""])
        source = row.get("source") or row.get("research") or {}
        for package in source.get("packages", []):
            lines.append(
                f"- 实际来源：{cell(package['name'])}=={cell(package['version'])}；"
                f"[PyPI 元数据]({package['metadata_url']})，"
                f"SHA-256 `{package['metadata_sha256']}`。"
            )
        if preparation := row.get("environment_preparation"):
            lines.append(
                f"- 环境准备尝试：{len(preparation['attempts'])} 次；采用修复："
                f"{sum(item['accepted'] for item in preparation['decisions'])} 次。"
            )
            for attempt in preparation["attempts"]:
                if attempt["error"]:
                    lines.append("- 历史准备失败：" + cell(attempt["error"][-1000:]))
        if "metrics" in row:
            lines.append("- 实际指标：" + json.dumps(row["metrics"], ensure_ascii=False))
            lines.append(
                f"- 程序执行墙钟：{row['execution_seconds']} 秒"
                "（包含启动/索引/预测，不含依赖安装）。"
            )
        if "generation" in row:
            generation = row["generation"]
            lines.append(
                f"- 共享模型 {cell(generation['model'])} 的真实生成耗时："
                f"{generation['generation_seconds']:.3f} 秒；"
                f"回答指标：{generation['metrics']}。"
            )
        study = row.get("research", {})
        if study.get("fit"):
            lines.append(study["fit"]["response"])
            for evidence in study["fit"]["evidence"]:
                lines.append(
                    f"- 来源摘录（{cell(evidence['package'])}/{evidence['field']}）："
                    + cell(evidence["quote"])
                )
        for outcome in row["tasks"]:
            if outcome["status"] != "completed":
                lines.append(
                    f"- {outcome['stage']}：{outcome['status']}；"
                    + cell(outcome["error"] or "未产生成功执行证据")
                )
        lines.append("")
    lines.append(
        "耗时为本次单机运行观测，受启动、缓存及并行竞争影响；不据此宣称通用性能排名或论文效果。"
    )
    markdown = "\n".join(lines)
    metrics = {
        "version": "framework.comparison/v1",
        "complete": comparable,
        "protocol": protocol.model_dump() if protocol else None,
        "frameworks": rows,
    }
    return TaskResult(
        result=markdown,
        outputs={
            "comparison_report": markdown,
            "comparison_metrics": json.dumps(metrics, ensure_ascii=False, allow_nan=False),
            "chat_answer": json.dumps({"response": markdown, "citations": []}, ensure_ascii=False),
        },
    )
