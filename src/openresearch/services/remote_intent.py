import json

import httpx
from pydantic import ValidationError

from openresearch.config import Settings
from openresearch.errors import ModelError
from openresearch.models.intent import IntentRequest
from openresearch.models.planning import IntentDraft


class RemoteIntentClient:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def classify(self, request: IntentRequest) -> IntentDraft:
        payload = request.model_dump(mode="json")
        payload["snapshot"]["turns"] = payload["snapshot"]["turns"][-6:]
        body = json.dumps(payload, ensure_ascii=False).encode()
        if len(body) > self.settings.llm_max_input_bytes:
            raise ModelError("intent context exceeds configured input limit")
        try:
            token = self.settings.intent_service_token.get_secret_value()
            async with (
                httpx.AsyncClient(
                    transport=self.transport,
                    trust_env=False,
                    timeout=httpx.Timeout(self.settings.llm_timeout_seconds, connect=3),
                    headers={"Authorization": "Bearer " + token} if token else {},
                ) as client,
                client.stream(
                    "POST",
                    self.settings.intent_service_url + "/v1/intent",
                    content=body,
                    headers={"Content-Type": "application/json"},
                ) as response,
            ):
                response.raise_for_status()
                output = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(output) + len(chunk) > 65536:
                        raise ModelError("intent service response exceeds configured limit")
                    output.extend(chunk)
                return IntentDraft.model_validate_json(bytes(output))
        except (httpx.HTTPError, ValidationError):
            raise ModelError(
                "optional intent service unavailable or returned an invalid contract"
            ) from None
