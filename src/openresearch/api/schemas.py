from typing import Literal

from pydantic import Field, field_validator

from openresearch.models.comparison import ComparisonSpec
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import ContractModel
from openresearch.models.submissions import Workflow


class CreatePlanRequest(ContractModel):
    intent: str = Field(min_length=1, max_length=16000)
    code: str | None = Field(default=None, max_length=131072)
    attachments: list[str] = Field(default_factory=list, max_length=8)
    workflow: Workflow = "auto"
    research: ResearchSpec | None = None
    comparison: ComparisonSpec | None = None
    planning_mode: Literal["auto", "llm", "template"] = "auto"

    @field_validator("intent")
    @classmethod
    def nonempty_intent(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("intent must not be blank")
        return value.strip()


class ChatRequest(ContractModel):
    message: str = Field(min_length=1, max_length=16000)

    @field_validator("message")
    @classmethod
    def nonempty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()


class MessageRequest(ChatRequest):
    attachments: list[str] = Field(default_factory=list, max_length=8)
