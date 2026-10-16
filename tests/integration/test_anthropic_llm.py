import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from openresearch.config import Settings
from openresearch.errors import ModelError
from openresearch.llm.client import LLMClient
from openresearch.llm.protocols import build_request


@pytest.mark.parametrize("suffix", ["", "/", "/v1", "/v1/"])
async def test_anthropic_system_authentication_and_text_blocks(
    settings: Settings, suffix: str
) -> None:
    settings.llm_provider = "anthropic"
    settings.llm_base_url = "https://provider.example/anthropic" + suffix
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    settings.llm_max_output_tokens = 2048

    def provider(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://provider.example/anthropic/v1/messages"
        assert request.headers["x-api-key"] == "private-token"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["model"] == "test-model" and body["max_tokens"] == 2048
        assert "response_format" not in body
        assert "Only generate code" in body["system"]
        assert body["messages"] == [{"role": "user", "content": "print seven"}]
        return httpx.Response(
            200,
            json={
                "type": "message",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "thinking", "thinking": "private reasoning"},
                    {"type": "text", "text": '{"code":'},
                    {"type": "text", "text": '"print(7)"}'},
                ],
            },
        )

    client = LLMClient(settings, httpx.MockTransport(provider))
    assert (
        await client.request_json(
            [
                {"role": "system", "content": "Only generate code"},
                {"role": "user", "content": "print seven"},
            ]
        )
        == '{"code":"print(7)"}'
    )


@pytest.mark.parametrize(
    ("content", "stop_reason"),
    [
        ([{"type": "text", "text": '{"code":"print(7)"}'}], "max_tokens"),
        ([{"type": "tool_use", "input": {"code": "print(7)"}}], "tool_use"),
        ([{"type": "thinking", "thinking": '{"code":"print(7)"}'}], "end_turn"),
        ([{"type": "text", "text": 7}], "end_turn"),
        ([{"type": "text", "text": 'explanation {"code":"print(7)"}'}], "end_turn"),
    ],
)
async def test_anthropic_invalid_or_truncated_output_is_rejected(
    settings: Settings, content: list[dict], stop_reason: str
) -> None:
    settings.llm_provider = "anthropic"
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    client = LLMClient(
        settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"type": "message", "content": content, "stop_reason": stop_reason}
            )
        ),
    )
    with pytest.raises(ModelError):
        await client.generate_code("print seven")


async def test_anthropic_complete_json_fence_is_supported(settings: Settings) -> None:
    settings.llm_provider = "anthropic"
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    client = LLMClient(
        settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "type": "message",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": '```json\n{"code":"print(7)"}\n```'}],
                },
            )
        ),
    )
    assert await client.generate_code("print seven") == "print(7)"


async def test_anthropic_http_failure_does_not_expose_secret(settings: Settings) -> None:
    settings.llm_provider = "anthropic"
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    client = LLMClient(
        settings,
        httpx.MockTransport(lambda _: httpx.Response(401, text="private-token provider details")),
    )
    with pytest.raises(ModelError, match="HTTP 401") as captured:
        await client.generate_code("print seven")
    assert "private-token" not in str(captured.value)


async def test_total_model_deadline_includes_response_wait(settings: Settings) -> None:
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    settings.llm_timeout_seconds = 1

    async def provider(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(ModelError, match="timed out"):
        await LLMClient(settings, httpx.MockTransport(provider)).generate_code("print seven")


@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
async def test_model_input_size_is_checked_before_network(
    settings: Settings, provider_name: str
) -> None:
    settings.llm_provider = provider_name  # type: ignore[assignment]
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    settings.llm_max_input_bytes = 4096

    def provider(_: httpx.Request) -> httpx.Response:
        raise AssertionError("oversized input must never reach the provider")

    with pytest.raises(ModelError, match="context limit"):
        await LLMClient(settings, httpx.MockTransport(provider)).generate_code("x" * 5000)


@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
async def test_model_response_size_limit_is_preserved(
    settings: Settings, provider_name: str
) -> None:
    settings.llm_provider = provider_name  # type: ignore[assignment]
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    settings.llm_max_response_bytes = 4096
    client = LLMClient(
        settings, httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 5000))
    )
    with pytest.raises(ModelError, match="size limit"):
        await client.generate_code("print seven")


def test_thinking_override_is_explicit_and_not_sent_by_default(settings: Settings) -> None:
    messages = [{"role": "user", "content": "generate code"}]
    assert "thinking" not in build_request(settings, messages).payload
    settings.llm_thinking = "disabled"
    assert build_request(settings, messages).payload["thinking"] == {"type": "disabled"}
    settings.llm_provider = "anthropic"
    assert build_request(settings, messages).payload["thinking"] == {"type": "disabled"}
