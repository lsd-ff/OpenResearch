from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import Field

from openresearch.models.planning import PendingPlanning
from openresearch.models.runtime import ContractModel, utcnow


@dataclass(frozen=True)
class Identity:
    user_id: str
    session_id: str


class ConversationTurn(ContractModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16000)
    created_at: datetime = Field(default_factory=utcnow)


class Conversation(ContractModel):
    owner_id: str
    session_id: str
    revision: int = 0
    uploads: list[str] = Field(default_factory=list, max_length=8)
    plans: list[str] = Field(default_factory=list, max_length=8)
    document_plans: list[str] = Field(default_factory=list, max_length=8)
    turns: list[ConversationTurn] = Field(default_factory=list, max_length=32)
    pending_planning: PendingPlanning | None = None
    updated_at: datetime = Field(default_factory=utcnow)
