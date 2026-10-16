import hashlib

from openresearch.config import Settings
from openresearch.llm.client import LLMClient
from openresearch.models.claims import (
    ClaimEvidenceGraph,
    ClaimResult,
    CriterionVerdict,
    EvidenceNode,
    FrozenClaim,
    FrozenCriterion,
    FrozenRubric,
)
from openresearch.models.research import ResearchReport
from openresearch.models.runtime import Plan
from openresearch.research.claim_verdicts import aggregate_status, numeric_verdict, unknown
from openresearch.research.rubrics import verify_rubric
from openresearch.research.semantic_claims import assess_semantic
from openresearch.storage.files import ArtifactStore


def criterion_verdict(
    criterion: FrozenCriterion,
    report: ResearchReport,
    evidence: dict[str, str],
    findings: dict[str, CriterionVerdict],
) -> CriterionVerdict:
    missing = [key for key in criterion.required_evidence if key not in evidence]
    if missing:
        return unknown(
            criterion, f"Missing required asset or evidence: {', '.join(missing)}", blocked=True
        )
    if criterion.metric_name:
        return numeric_verdict(criterion, report)
    return findings.get(criterion.id) or unknown(
        criterion, "No validated semantic execution evidence."
    )


def claim_result(claim: FrozenClaim, verdicts: list[CriterionVerdict]) -> ClaimResult:
    return ClaimResult(
        claim_id=claim.id,
        title=claim.title,
        statement=claim.statement,
        source_locator=claim.source_locator,
        claim_type=claim.claim_type,
        status=aggregate_status(verdicts),
        confidence=min(item.confidence for item in verdicts),
        criteria=verdicts,
    )


def evidence_summary(claims: list[ClaimResult]) -> dict[str, int | float]:
    coverage = [criterion for claim in claims for criterion in claim.criteria]
    statuses = (
        "verified",
        "partially_reproduced",
        "contradicted",
        "unverifiable",
        "blocked_by_missing_asset",
    )
    return {
        "total_claims": len(claims),
        "total_criteria": len(coverage),
        **{status: sum(claim.status == status for claim in claims) for status in statuses},
        "criterion_evidence_coverage": sum(bool(item.evidence_ids) for item in coverage)
        / len(coverage)
        if coverage
        else 0,
    }


class ClaimAssessor:
    def __init__(self, settings: Settings, files: ArtifactStore) -> None:
        self.files = files
        self.llm = LLMClient(settings)

    async def assess(
        self, plan: Plan, report: ResearchReport, outputs: dict[str, str]
    ) -> ClaimEvidenceGraph:
        rubric = FrozenRubric.model_validate_json(self.files.read(plan.artifacts["claim_rubric"]))
        verify_rubric(rubric)
        evidence = {key: self.files.read(artifact) for key, artifact in plan.artifacts.items()}
        evidence.update(outputs)
        semantic = [
            criterion
            for claim in rubric.claims
            for criterion in claim.criteria
            if criterion.metric_name is None
            and all(key in evidence for key in criterion.required_evidence)
        ]
        findings = await assess_semantic(self.llm, semantic, evidence) if report.success else {}
        claims = [
            claim_result(
                claim,
                [criterion_verdict(item, report, evidence, findings) for item in claim.criteria],
            )
            for claim in rubric.claims
        ]
        return ClaimEvidenceGraph(
            status_reason=(
                "Frozen criteria assessed against immutable sources and execution evidence."
            ),
            rubric_sha256=rubric.sha256,
            claims=claims,
            evidence=[
                EvidenceNode(
                    id=f"evidence:{key}",
                    artifact_key=key,
                    evidence_type="execution"
                    if key in {"research_report", "benchmark_predictions"}
                    else "source",
                    sha256=hashlib.sha256(text.encode()).hexdigest(),
                    summary=text[:500],
                )
                for key, text in evidence.items()
            ],
            summary=evidence_summary(claims),
        )


def verification_markdown(graph: ClaimEvidenceGraph) -> str:
    lines = [
        "# 主张验收报告",
        "",
        f"冻结规则 SHA-256：`{graph.rubric_sha256}`",
        "",
        "结论只适用于已声明的数据集、参数和运行环境。",
        "",
    ]
    for claim in graph.claims:
        lines.extend(
            [
                f"## {claim.title}：{claim.status}",
                "",
                claim.statement,
                f"来源：{claim.source_locator}",
                "",
            ]
        )
        lines.extend(
            f"- {item.criterion_id}：{item.status}；{item.reason}" for item in claim.criteria
        )
        lines.append("")
    return "\n".join(lines)
