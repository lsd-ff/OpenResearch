import base64
import json
import math
from html import escape

from openresearch.errors import ContractError
from openresearch.models.audit import ResultAudit
from openresearch.models.claims import ClaimEvidenceGraph
from openresearch.models.research import ResearchReport


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def review_markdown(audit: ResultAudit, report: ResearchReport | None = None) -> str:
    lines = [
        "# 实际结果复核",
        "",
        f"源计划：`{audit.source_plan_id}`；源节点：`{audit.source_task_id}`。",
        f"原节点状态：{audit.source_status}；复核状态：{audit.status}。",
        "",
        audit.scope,
        "",
    ]
    if audit.dataset_sha256:
        lines.extend(
            [f"实际数据 SHA-256：`{audit.dataset_sha256}`；样本数：{audit.sample_count}。", ""]
        )
    if report:
        lines.extend(
            [
                f"仓库提交：`{report.repository_commit or '未声明仓库'}`；"
                f"入口：`{cell(report.entrypoint)}`。",
                "",
            ]
        )
        for run in report.runs:
            final = run.attempts[-1]
            python = run.environment.python if run.environment else "未取得安装回执"
            lines.append(
                f"- {cell(run.variant)}：实际尝试 {len(run.attempts)} 次，"
                f"最终退出码 {final.exit_code}；Python {python}。"
            )
            if final.error:
                lines.append("  实际失败：" + cell(final.error[:1200]))
        lines.append("")
    lines.extend(["| 实际运行/列 | 指标 | 核对值 |", "| --- | --- | --- |"])
    lines.extend(
        f"| {cell(variant)} | {cell(key)} | {value:.12g} |"
        for variant, values in audit.metrics.items()
        for key, value in values.items()
    )
    lines.extend(["", "## 复核与局限", ""])
    lines.extend("- " + value for value in [*audit.checks, *audit.limitations, *audit.errors])
    return "\n".join(lines)


def comparison_markdown(
    audit: ResultAudit, report: ResearchReport, graph: ClaimEvidenceGraph
) -> str:
    lines = [
        review_markdown(audit, report),
        "",
        "## 对照冻结主张",
        "",
        "| 主张 | 验收条件 | 判定 | 实际观察 |",
        "| --- | --- | --- | --- |",
    ]
    for claim in graph.claims:
        lines.extend(
            f"| {cell(claim.title)} | {cell(item.description)} | {item.status} | "
            f"{cell(item.observed_value or item.reason)} |"
            for item in claim.criteria
        )
    if not graph.claims:
        lines.append("未冻结可独立验收的论文主张；仅有运行结果，不能据此认定论文复现完成。")
    return "\n".join(lines)


def plot_panels(
    audit: ResultAudit, report: ResearchReport | None
) -> dict[str, list[tuple[str, float]]]:
    metrics = audit.metrics
    if not any(metrics.values()) and report:
        metrics = {
            run.variant: {"execution_attempts": float(len(run.attempts))} for run in report.runs
        }
    panels: dict[str, list[tuple[str, float]]] = {}
    for variant, measurements in metrics.items():
        for key, value in measurements.items():
            if not math.isfinite(value):
                raise ContractError("plot values must be finite actual measurements")
            panels.setdefault(key, []).append((variant, value))
    return {key: values[:32] for key, values in list(panels.items())[:8]}


def panel_shapes(metric: str, series: list[tuple[str, float]], y: int) -> tuple[list[str], int]:
    extent = max(abs(value) for _, value in series) or 1.0
    low = min(0.0, *(value / extent for _, value in series))
    high = max(0.0, *(value / extent for _, value in series))
    scale = 480 / (high - low) if high != low else 1.0
    zero = 240 - low * scale
    shapes = [f'<text x="20" y="{y}">{escape(metric)}</text>']
    y += 18
    for variant, value in series:
        endpoint = zero + value / extent * scale
        shapes.extend(
            [
                f'<text x="20" y="{y + 14}">{escape(variant[:22])}</text>',
                f'<rect x="{min(zero, endpoint):.4f}" y="{y}" '
                f'width="{abs(endpoint - zero):.4f}" height="18" fill="#2563eb"/>',
                f'<text x="750" y="{y + 14}">{value:.10g}</text>',
            ]
        )
        y += 28
    return shapes, y + 35


def plot_results(audit: ResultAudit, report: ResearchReport | None) -> tuple[str, str, str]:
    panels = plot_panels(audit, report)
    if not panels:
        return "", "", ""
    height = 110 + sum(65 + 28 * len(values) for values in panels.values())
    shapes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}" '
        'role="img" aria-label="Actual result metrics">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<g font-family="sans-serif" font-size="18" fill="#172554">',
        '<text x="20" y="28">Actual saved results; no new experiment</text>',
        f'<text x="20" y="50">Source status: {escape(str(audit.source_status))}; '
        f"audit: {audit.status}</text>",
    ]
    y = 80
    for metric, series in panels.items():
        panel, y = panel_shapes(metric, series, y)
        shapes.extend(panel)
    shapes.extend(["</g>", "</svg>"])
    svg = "".join(shapes)
    specification = json.dumps(
        {
            "source_plan_id": audit.source_plan_id,
            "source_task_id": audit.source_task_id,
            "panels": panels,
            "scope": "Actual recorded metrics/counts; each metric has its own scale; "
            "at most 8 panels/32 entries.",
        },
        ensure_ascii=False,
        allow_nan=False,
    )
    return (
        svg,
        specification,
        "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode(),
    )
