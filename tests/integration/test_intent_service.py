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
        assert client.get("/health").json() == {"status": "ok"}
        assert client.post("/predict", json={"text": "smoke"}).status_code == 401
        assert client.post("/v1/intent", json=payload).status_code == 401
        response = client.post(
            "/v1/intent", json=payload, headers={"Authorization": "Bearer intent-only-key"}
        )
        assert response.status_code == 200
        assert IntentDraft.model_validate(response.json()).workflow == "code"


async def test_legacy_intent_uses_real_parser_contract_and_propagates_model_errors(settings):
    settings.llm_model, settings.llm_api_key = "test-model", SecretStr("test-key")
    app = create_intent_app(settings)
    requests = []

    def classify(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"workflow":"reproduce"}'}}]},
        )

    app.state.parser.llm = LLMClient(settings, httpx.MockTransport(classify))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://intent.test"
    ) as client:
        response = await client.post("/predict", json={"text": "复现论文并运行测试"})
        assert response.status_code == 200
        assert response.json() == {
            "intent_type": "Paper_Reproduction",
            "confidence": 0.0,
            "entities": {},
        }
        assert len(requests) == 1
        assert (await client.post("/predict", json={"text": ""})).status_code == 422
        app.state.parser.llm = LLMClient(
            settings, httpx.MockTransport(lambda request: httpx.Response(503))
        )
        assert (
            await client.post("/predict", json={"text": "复现论文并运行测试"})
        ).status_code == 503
