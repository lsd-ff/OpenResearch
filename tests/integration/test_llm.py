import json

import httpx
import pytest
from pydantic import SecretStr

from openresearch.config import Settings
from openresearch.errors import ModelError
from openresearch.llm.client import LLMClient


async def test_compatible_provider_request_and_typed_code(settings: Settings) -> None:
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")

    def provider(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer private-token"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"code":"print(7)"}'}}]}
        )

    client = LLMClient(settings, httpx.MockTransport(provider))
    assert await client.generate_code("print seven") == "print(7)"


@pytest.mark.parametrize("failure", ["bad_json", "bad_contract", "http_error", "connection"])
async def test_provider_failures_are_sanitized(settings: Settings, failure: str) -> None:
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")

    def provider(request: httpx.Request) -> httpx.Response:
        if failure == "connection":
            raise httpx.ConnectError("private-token provider details", request=request)
        if failure == "http_error":
            return httpx.Response(401, text="private-token provider details")
        if failure == "bad_json":
            return httpx.Response(200, text="private-token provider details")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"code":"","unknown":true}'}}]}
        )

    client = LLMClient(settings, httpx.MockTransport(provider))
    with pytest.raises(ModelError) as captured:
        await client.generate_code("intent")
    assert "private-token" not in str(captured.value)


async def test_contract_diagnostics_expose_types_and_known_fields_without_response_data(
    settings, caplog
):
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    client = LLMClient(
        settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": '{"code":null,"private-token":"secret-body"}'}}
                    ]
                },
            )
        ),
    )
    with pytest.raises(ModelError):
        await client.generate_code("create a program")
    errors = [
        record for record in caplog.records if record.msg == "model contract validation failed"
    ]
    assert errors and all(
        "string_type" in record.error_type and ":code" in record.error_type for record in errors
    )
    assert all(
        "private-token" not in record.error_type and "secret-body" not in record.error_type
        for record in errors
    )
