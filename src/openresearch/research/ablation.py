import re
from pathlib import PurePosixPath

from openresearch.errors import ContractError
from openresearch.models.ablation import (
    AblationBudget,
    AblationCandidate,
    AblationCandidates,
    AblationEvaluation,
    AblationPlan,
    ScoredAblation,
)


def requested_ablation_budget(intent: str) -> AblationBudget | None:
    intent = re.sub(
        r"(?:不做|不需要|无需|不用|不要|without|no)\s*(?:做|运行)?\s*(?:消融|ablations?)",
        "",
        intent,
        flags=re.I,
    )
    if not re.search(r"消融|\bablation\b|参数敏感|模块移除|随机种子|seed stability", intent, re.I):
        return None
    experiments = re.search(
        r"(?:最多|max(?:imum)?)\s*(\d+)\s*(?:组|个|次)?\s*"
        r"(?:轻量|小型|bounded)?\s*(?:消融|ablations?)",
        intent,
        re.I,
    )
    wall = re.search(r"(?:墙钟|wall)[^\d]{0,16}(\d+)\s*(?:分钟|minutes?)", intent, re.I)
    gpu = re.search(r"gpu[^\d]{0,16}(\d+)\s*(?:分钟|minutes?)", intent, re.I)
    return AblationBudget(
        max_experiments=max(1, min(6, int(experiments[1]))) if experiments else 3,
        max_wall_minutes=max(1, min(1440, int(wall[1]))) if wall else 60,
        max_gpu_minutes=max(0, min(1440, int(gpu[1]))) if gpu else 0,
    )


def adapter_argument_error(proposal: AblationCandidates, entrypoint: str) -> str:
    for candidate in proposal.candidates:
        if not candidate.arguments:
            continue
        first = candidate.arguments[0].removeprefix("./")
        if first in {entrypoint, PurePosixPath(entrypoint).name} or re.fullmatch(
            r"python(?:[0-9]+(?:\.[0-9]+)*)?(?:\.exe)?", first, re.I
        ):
            return (
                f"Candidate {candidate.id}: arguments must contain application arguments only; "
                "the launcher already supplies Python and the entrypoint. Remove the launcher "
                "and script path, preserving supported flags and values."
            )
    return ""


def score_candidate(
    candidate: AblationCandidate, evaluation: AblationEvaluation | None, budget: AblationBudget
) -> ScoredAblation:
    rating = evaluation or AblationEvaluation(
        id=candidate.id,
        information_gain=0.6,
        relevance=0.6,
        reproducibility=0.8,
        risk=0.1,
        reason="Model scoring unavailable; deterministic prior, not an observed result.",
    )
    cost = min(1, candidate.estimated_minutes / budget.max_wall_minutes)
    score = round(
        0.4 * rating.information_gain
        + 0.3 * rating.relevance
        + 0.2 * rating.reproducibility
        - 0.07 * cost
        - 0.03 * rating.risk,
        4,
    )
    return ScoredAblation(
        **candidate.model_dump(),
        **rating.model_dump(exclude={"id", "reason"}),
        score=score,
        evaluation_reason=rating.reason,
    )


def select_candidates(
    proposal: AblationCandidates, evaluations: list[AblationEvaluation], budget: AblationBudget
) -> AblationPlan:
    ratings = {item.id: item for item in evaluations}
    if evaluations and (
        len(ratings) != len(evaluations)
        or set(ratings) != {item.id for item in proposal.candidates}
    ):
        raise ContractError("ablation scores must cover every real candidate exactly once")
    ranked = sorted(
        [score_candidate(item, ratings.get(item.id), budget) for item in proposal.candidates],
        key=lambda item: (-item.score, item.estimated_minutes, item.id),
    )
    selected: list[ScoredAblation] = []
    pruned: list[str] = []
    wall, gpu = 0, 0
    categories: set[str] = set()
    for item in ranked:
        if (
            len(selected) >= budget.max_experiments
            or item.category in categories
            or wall + item.estimated_minutes > budget.max_wall_minutes
            or gpu + item.estimated_gpu_minutes > budget.max_gpu_minutes
        ):
            pruned.append(item.id)
            continue
        selected.append(item)
        categories.add(item.category)
        wall += item.estimated_minutes
        gpu += item.estimated_gpu_minutes
    return AblationPlan(
        budget=budget,
        candidates=ranked,
        selected=selected,
        pruned_ids=pruned,
        selection_reason=(
            f"Selected {len(selected)} diverse branches; estimated wall={wall}/"
            f"{budget.max_wall_minutes}, GPU={gpu}/{budget.max_gpu_minutes} minutes. "
            "Scores and costs are design estimates, not measured experiment results."
        ),
        scoring_source="model" if evaluations else "deterministic_prior",
    )
