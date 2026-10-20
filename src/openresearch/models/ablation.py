from typing import Annotated, Literal

from pydantic import Field, model_validator

from openresearch.models.runtime import ContractModel

CandidateId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
UnitScore = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
AblationCategory = Literal["parameter", "module", "data_scale", "seed_stability", "runtime_cost"]


class AblationBudget(ContractModel):
    max_experiments: int = Field(default=3, ge=1, le=6)
    max_gpu_minutes: int = Field(default=0, ge=0, le=1440)
    max_wall_minutes: int = Field(default=60, ge=1, le=1440)


class AblationCandidate(ContractModel):
    id: CandidateId
    parent_id: CandidateId | None = None
    category: AblationCategory
    title: str = Field(min_length=1, max_length=200)
    hypothesis: str = Field(min_length=1, max_length=1000)
    change: str = Field(min_length=1, max_length=1000)
    arguments: list[Annotated[str, Field(max_length=4096)]] = Field(max_length=16)
    estimated_minutes: int = Field(ge=1, le=1440)
    estimated_gpu_minutes: int = Field(default=0, ge=0, le=1440)


class AblationCandidates(ContractModel):
    candidates: list[AblationCandidate] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def bounded_tree(self) -> "AblationCandidates":
        nodes = {item.id: item for item in self.candidates}
        if len(nodes) != len(self.candidates) or "baseline" in nodes:
            raise ValueError("ablation IDs must be unique and exclude baseline")
        for node in nodes.values():
            if node.parent_id is None:
                continue
            parent = nodes.get(node.parent_id)
            if parent is None or parent.id == node.id or parent.parent_id is not None:
                raise ValueError("ablation tree parents must exist with depth at most two")
        return self


class AblationEvaluation(ContractModel):
    id: CandidateId
    information_gain: UnitScore
    relevance: UnitScore
    reproducibility: UnitScore
    risk: UnitScore
    reason: str = Field(min_length=1, max_length=1000)


class AblationEvaluations(ContractModel):
    evaluations: list[AblationEvaluation] = Field(min_length=1, max_length=8)


class ScoredAblation(AblationCandidate):
    information_gain: UnitScore
    relevance: UnitScore
    reproducibility: UnitScore
    risk: UnitScore
    score: float
    evaluation_reason: str


class AblationPlan(ContractModel):
    version: Literal["ablation.plan/v1"] = "ablation.plan/v1"
    strategy: Literal["bounded_tree_of_thoughts"] = "bounded_tree_of_thoughts"
    max_depth: Literal[2] = 2
    branch_limit: Literal[8] = 8
    budget: AblationBudget
    candidates: list[ScoredAblation]
    selected: list[ScoredAblation]
    pruned_ids: list[str]
    selection_reason: str
    generation_source: Literal["model", "design_fallback", "source_fallback"] = "model"
    scoring_source: Literal["model", "deterministic_prior"] = "model"
