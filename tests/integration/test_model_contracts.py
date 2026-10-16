import asyncio
import json
from typing import Literal

import httpx
import pytest
from pydantic import SecretStr

from openresearch.config import Settings
from openresearch.errors import ModelError
from openresearch.llm.client import LLMClient
from openresearch.models.chat import ChatReply
from openresearch.models.claims import RubricDraft


def provider_response(protocol: str, value: dict) -> httpx.Response:
    text = json.dumps(value)
    if protocol == "anthropic":
        return httpx.Response(
            200,
            json={"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]},
        )
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_typed_requests_share_schema_and_correct_only_invalid_contract(
    settings: Settings, protocol: Literal["openai", "anthropic"]
) -> None:
    settings.llm_provider = protocol
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    calls = 0
    criterion = {"description": "verify output", "required_evidence": "execution report"}
    draft = {"claims": [{"title": "claim", "statement": "claim", "criteria": [criterion]}]}

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        system = body.get("system") or body["messages"][0]["content"]
        schema = json.loads(system.split("JSON Schema exactly:\n", 1)[1].split("\n\n", 1)[0])
        assert (
            schema["$defs"]["CriterionDraft"]["properties"]["required_evidence"]["type"] == "array"
        )
        if calls == 2:
            assert "required_evidence" in body["messages"][-1]["content"]
            criterion["required_evidence"] = ["research_report"]
        return provider_response(protocol, draft)

    client = LLMClient(settings, httpx.MockTransport(provider))
    messages = [{"role": "user", "content": "extract claims"}]
    rubric = await client.request_typed(messages, RubricDraft)
    assert rubric.claims[0].criteria[0].required_evidence == ["research_report"]
    assert calls == 2 and messages == [{"role": "user", "content": "extract claims"}]


async def test_invalid_contract_retry_is_bounded_and_sanitized(settings: Settings) -> None:
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    calls = 0

    def provider(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return provider_response("openai", {"code": "", "private-token": "secret"})

    with pytest.raises(ModelError, match="response contract") as captured:
        await LLMClient(settings, httpx.MockTransport(provider)).generate_code("print seven")
    assert calls == 2 and "private-token" not in str(captured.value)


async def test_http_error_is_not_retried_as_a_contract_error(settings: Settings) -> None:
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    calls = 0

    def provider(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, text="private-token provider details")

    with pytest.raises(ModelError, match="HTTP 429"):
        await LLMClient(settings, httpx.MockTransport(provider)).generate_code("print seven")
    assert calls == 1


async def test_contract_correction_shares_the_original_deadline(settings: Settings) -> None:
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    settings.llm_timeout_seconds = 1
    calls = 0

    async def provider(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.6)
        return provider_response("openai", {"code": ""})

    with pytest.raises(ModelError, match="timed out"):
        await LLMClient(settings, httpx.MockTransport(provider)).generate_code("print seven")
    assert calls == 2


async def test_ignored_response_tool_plain_text_is_corrected_without_parsing_prose(
    settings: Settings,
) -> None:
    settings.llm_provider = "anthropic"
    settings.llm_structured_output = "tool"
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    calls = 0

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "模型已连接，可以开始使用。"}],
                },
            )
        assert body["messages"][-2]["content"] == "模型已连接，可以开始使用。"
        assert "not a complete JSON object" in body["messages"][-1]["content"]
        assert body["tool_choice"] == {"type": "tool", "name": "submit_response"}
        return httpx.Response(
            200,
            json={
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "submit_response",
                        "input": {"response": "模型已连接，可以开始使用。", "citations": []},
                    }
                ],
            },
        )

    reply = await LLMClient(settings, httpx.MockTransport(provider)).request_typed(
        [{"role": "user", "content": "请简短回复"}], ChatReply
    )
    assert reply.response == "模型已连接，可以开始使用。" and calls == 2


async def test_invalid_json_correction_is_bounded_and_does_not_expose_output(
    settings: Settings,
) -> None:
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    calls = 0

    def provider(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "private-token invalid JSON"}}]}
        )

    with pytest.raises(ModelError, match="response contract") as captured:
        await LLMClient(settings, httpx.MockTransport(provider)).generate_code("print seven")
    assert calls == 2 and "private-token" not in str(captured.value)
