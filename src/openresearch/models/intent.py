from typing import Any

from pydantic import Field

from openresearch.models.context import Conversation
from openresearch.models.runtime import ContractModel


class IntentRequest(ContractModel):
    snapshot: Conversation
    message: str = Field(min_length=1, max_length=16000)
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
