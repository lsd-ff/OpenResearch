from openresearch.models.claims import CriterionVerdict, FrozenCriterion, VerdictStatus
from openresearch.models.research import ResearchReport


def unknown(criterion: FrozenCriterion, reason: str, blocked: bool = False) -> CriterionVerdict:
    return CriterionVerdict(
        criterion_id=criterion.id,
        description=criterion.description,
        status="blocked_by_missing_asset" if blocked else "unverifiable",
        confidence=0,
        reason=reason,
    )


def numeric_verdict(criterion: FrozenCriterion, report: ResearchReport) -> CriterionVerdict:
    run = next((run for run in report.runs if run.variant == criterion.variant), None)
    if run is None or criterion.metric_name not in run.metrics:
        return unknown(
            criterion, "Declared metric is unavailable in the validated experiment output."
        )
    if run.attempts[-1].error:
        return unknown(criterion, "Experiment did not produce a valid completed prediction set.")
    observed = run.metrics[criterion.metric_name]
    expected = criterion.expected_value
    assert expected is not None
    passed = abs(observed - expected) <= criterion.tolerance
    if criterion.operator == "at_least":
        passed = observed >= expected - criterion.tolerance
    elif criterion.operator == "at_most":
        passed = observed <= expected + criterion.tolerance
    return CriterionVerdict(
        criterion_id=criterion.id,
        description=criterion.description,
        status="verified" if passed else "contradicted",
        confidence=1,
        observed_value=str(observed),
        evidence_ids=["evidence:research_report", "evidence:benchmark_predictions"],
        reason=(
            f"Server-recomputed {criterion.variant}/{criterion.metric_name}; "
            f"frozen {criterion.operator} {expected} with tolerance {criterion.tolerance}. "
            "Applies to the declared dataset and protocol."
        ),
    )


def aggregate_status(verdicts: list[CriterionVerdict]) -> VerdictStatus:
    statuses = {verdict.status for verdict in verdicts}
    if statuses == {"verified"}:
        return "verified"
    if "verified" in statuses or "partially_reproduced" in statuses:
        return "partially_reproduced"
    if "contradicted" in statuses:
        return "contradicted"
    if "blocked_by_missing_asset" in statuses:
        return "blocked_by_missing_asset"
    return "unverifiable"
