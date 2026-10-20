from dataclasses import dataclass, field
from typing import Literal

from openresearch.models.comparison import ComparisonSpec
from openresearch.models.context import Identity
from openresearch.models.research import ResearchSpec

Workflow = Literal["auto", "code", "paper", "reproduce", "benchmark", "data", "framework"]


@dataclass(frozen=True)
class PlanSubmission:
    intent: str
    identity: Identity
    code: str | None = None
    attachments: list[str] = field(default_factory=list)
    workflow: Workflow = "auto"
    research: ResearchSpec | None = None
    auto_design: bool = False
    comparison: ComparisonSpec | None = None
