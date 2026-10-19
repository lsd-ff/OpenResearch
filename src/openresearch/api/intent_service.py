from fastapi import FastAPI

from openresearch.api.body_limit import BodyLimitMiddleware
from openresearch.api.middleware import install_error_handlers, install_middleware
from openresearch.config import Settings
from openresearch.models.intent import IntentRequest
from openresearch.models.planning import IntentDraft
from openresearch.services.intent import IntentParser


def create_intent_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings or Settings()
    app = FastAPI(title="OpenResearch Optional Intent Service")
    install_error_handlers(app)
    install_middleware(
        app, configuration.model_copy(update={"api_token": configuration.intent_service_token})
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=configuration.llm_max_input_bytes + 4096)
    # 服务端禁用远程识别，避免部署时继承 API 配置后递归调用自己。
    parser = IntentParser(configuration.model_copy(update={"intent_service_url": ""}))
    app.state.parser = parser

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/intent")
    async def classify(request: IntentRequest) -> IntentDraft:
        return await parser.parse(request.snapshot, request.message, request.attachments)

    return app
