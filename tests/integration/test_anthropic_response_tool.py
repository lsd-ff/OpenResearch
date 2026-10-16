import json

import httpx
import pytest
from pydantic import SecretStr

from openresearch.config import Settings
from openresearch.errors import ModelError
from openresearch.llm.client import LLMClient


async def test_named_response_tool_returns_validated_code_data(settings: Settings) -> None:
    settings.llm_provider = "anthropic"
    settings.llm_structured_output = "tool"
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")

    def provider(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tool_choice"] == {"type": "tool", "name": "submit_response"}
        assert len(body["tools"]) == 1
        assert body["tools"][0]["input_schema"]["properties"]["code"]["type"] == "string"
        return httpx.Response(
            200,
            json={
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "name": "submit_response", "input": {"code": "print(7)"}}
                ],
            },
        )

    assert (
        await LLMClient(settings, httpx.MockTransport(provider)).generate_code("print seven")
        == "print(7)"
    )


@pytest.mark.parametrize("failure", ["unknown", "duplicate", "invalid_input", "truncated"])
async def test_unexpected_or_truncated_response_tools_are_rejected(
    settings: Settings, failure: str
) -> None:
    settings.llm_provider = "anthropic"
    settings.llm_structured_output = "tool"
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    tool = {"type": "tool_use", "name": "submit_response", "input": {"code": "print(7)"}}
    if failure == "unknown":
        tool["name"] = "execute_shell"
    if failure == "invalid_input":
        tool["input"] = "print(7)"
    content = [tool, tool] if failure == "duplicate" else [tool]
    client = LLMClient(
        settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "stop_reason": "max_tokens" if failure == "truncated" else "tool_use",
                    "content": content,
                },
            )
        ),
    )
    with pytest.raises(ModelError):
        await client.generate_code("print seven")
