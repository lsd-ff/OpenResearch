from typing import Any

from pydantic import Field

from openresearch.models.context import Conversation
from openresearch.models.runtime import ContractModel


class IntentRequest(ContractModel):
    snapshot: Conversation
    message: str = Field(min_length=1, max_length=16000)
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=8)


class LegacyIntentRequest(ContractModel):
    text: str = Field(min_length=1, max_length=16000)


class LegacyIntentResponse(ContractModel):
    intent_type: str
    # 当前分类器不产出校准概率；0 明确表示未知，不沿用旧模拟服务的 0.95。
    confidence: float = Field(default=0.0, ge=0, le=1)
    entities: dict[str, Any] = Field(default_factory=dict)
