import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openresearch.api.intent_service import create_intent_app
from openresearch.llm.client import LLMClient
from openresearch.models.context import Conversation
from openresearch.models.intent import IntentRequest
from openresearch.models.planning import IntentDraft
from openresearch.services.intent import IntentParser
from openresearch.services.remote_intent import RemoteIntentClient


async def test_remote_classifier_uses_separate_token_and_does_not_send_model_secrets(settings):
    settings.intent_service_url = "http://intent.test"
    settings.intent_service_token = SecretStr("intent-only-key")
    settings.llm_api_key = SecretStr("never-forward-model-key")

    def handle(request):
        assert request.headers["Authorization"] == "Bearer intent-only-key"
        assert b"never-forward-model-key" not in request.content
        return httpx.Response(200, json={"workflow": "data"})

    parser = IntentParser(settings)
    parser.remote = RemoteIntentClient(settings, httpx.MockTransport(handle))
    draft = await parser.parse(Conversation(owner_id="a", session_id="s"), "分析数据", [])
    assert draft.workflow == "data"


@pytest.mark.parametrize("failure", ["network", "contract"])
async def test_unavailable_optional_service_falls_back_to_local_model(settings, failure):
    settings.intent_service_url = "http://intent.test"
    settings.llm_model, settings.llm_api_key = "test-model", SecretStr("test-key")

    def remote(request):
        return httpx.Response(503 if failure == "network" else 200, json={"unexpected": True})

    local = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"workflow":"data"}'}}],
            },
        )
    )
    parser = IntentParser(settings)
    parser.remote = RemoteIntentClient(settings, httpx.MockTransport(remote))
    parser.llm = LLMClient(settings, local)
    assert (
        await parser.parse(Conversation(owner_id="a", session_id="s"), "分析数据", [])
    ).workflow == "data"


def test_optional_service_auth_contract_and_recursion_prevention(settings):
    settings.intent_service_token = SecretStr("intent-only-key")
    settings.intent_service_url = "http://self"
    app = create_intent_app(settings)
    payload = IntentRequest(
        snapshot=Conversation(owner_id="a", session_id="s"), message="smoke"
    ).model_dump(mode="json")
    with TestClient(app) as client:
        assert app.state.parser.settings.intent_service_url == ""
        assert client.post("/v1/intent", json=payload).status_code == 401
        response = client.post(
            "/v1/intent", json=payload, headers={"Authorization": "Bearer intent-only-key"}
        )
        assert response.status_code == 200
        assert IntentDraft.model_validate(response.json()).workflow == "code"
