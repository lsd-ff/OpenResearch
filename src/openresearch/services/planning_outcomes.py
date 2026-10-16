from dataclasses import dataclass

from openresearch.models.context import Conversation
from openresearch.models.planning import PendingPlanning
from openresearch.models.runtime import Plan


@dataclass
class PlanningOutcome:
    snapshot: Conversation
    response: str
    plan: Plan | None = None
    pending: PendingPlanning | None = None
    chat: bool = False


def planning_question(
    snapshot: Conversation, state: PendingPlanning, question: str
) -> PlanningOutcome:
    state.question = question
    return PlanningOutcome(snapshot, question, pending=state)
