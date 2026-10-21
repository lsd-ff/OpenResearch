from fastapi import FastAPI

from openresearch.api.body_limit import BodyLimitMiddleware
from openresearch.api.middleware import install_error_handlers, install_middleware
from openresearch.config import Settings
from openresearch.models.context import Conversation
from openresearch.models.intent import IntentRequest, LegacyIntentRequest, LegacyIntentResponse
from openresearch.models.planning import IntentDraft
from openresearch.services.intent import IntentParser

LEGACY_INTENTS = {
    "chat": "General_Query",
    "code": "Code_Execution",
    "paper": "Literature_Review",
    "reproduce": "Paper_Reproduction",
    "benchmark": "Framework_Evaluation",
    "data": "Data_Analysis",
    "framework": "Framework_Evaluation",
}


def create_intent_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings or Settings()
    app = FastAPI(title="OpenResearch Optional Intent Service")
    install_error_handlers(app)
    install_middleware(
        app,
        configuration.model_copy(update={"api_token": configuration.intent_service_token}),
        public_paths=frozenset({"/ping", "/health"}),
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=configuration.llm_max_input_bytes + 4096)
    # 服务端禁用远程识别，避免部署时继承 API 配置后递归调用自己。
    parser = IntentParser(configuration.model_copy(update={"intent_service_url": ""}))
    app.state.parser = parser

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/predict")
    async def predict(request: LegacyIntentRequest) -> LegacyIntentResponse:
        # 旧协议没有会话字段：每次独立分类，不在固定身份下积累不同用户的记忆。
        draft = await parser.parse(
            Conversation(owner_id="intent-service", session_id="stateless"), request.text, []
        )
        return LegacyIntentResponse(
            intent_type=LEGACY_INTENTS[draft.workflow],
            entities=draft.model_dump(exclude={"workflow"}, exclude_defaults=True),
        )

    @app.post("/v1/intent")
    async def classify(request: IntentRequest) -> IntentDraft:
        return await parser.parse(request.snapshot, request.message, request.attachments)

    return app
