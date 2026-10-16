from typing import Any

from openresearch.models.runtime import Plan
from openresearch.services.messages import MessageResult


def plan_payload(plan: Plan) -> dict[str, Any]:
    return {**plan.model_dump(mode="json", by_alias=True), "meta": plan.meta}


def message_payload(result: MessageResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": result.kind,
        "response": result.response,
        "context_revision": result.revision,
        "citations": [citation.model_dump() for citation in result.citations],
    }
    if result.plan is not None:
        payload["plan_graph"] = plan_payload(result.plan)
        payload["intent_context"] = {
            "raw_intent": result.plan.user_intent,
            "intent_type": result.plan.intent_type,
            "entities": {},
            "constraints": {},
            "metadata": {"planning_mode": "conversation"},
        }
    if result.kind == "clarification":
        payload["clarification"] = {
            "required": True,
            "type": "conversation",
            "question": result.response,
        }
    return payload
