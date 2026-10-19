import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from openresearch import __version__
from openresearch.api import (
    conversations,
    documents,
    events,
    plans,
    sources,
    system,
    tasks,
    uploads,
)
from openresearch.api.body_limit import BodyLimitMiddleware
from openresearch.api.dependencies import Runtime
from openresearch.api.middleware import install_error_handlers, install_middleware
from openresearch.config import Settings
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.factory import build_sandbox
from openresearch.services.auto_planning import AutoPlanner
from openresearch.services.chat import ChatService
from openresearch.services.messages import MessageService
from openresearch.services.plans import PlanService
from openresearch.services.uploads import UploadService
from openresearch.sources.discovery import SourceDiscovery
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


def create_app(
    settings: Settings | None = None,
    repository: Repository | None = None,
    sandbox: Sandbox | None = None,
    discovery: SourceDiscovery | None = None,
) -> FastAPI:
    """组装 API；测试可注入存储和沙箱，生产启动由 lifespan 管理资源。"""
    configuration = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        store: Repository = (
            repository
            if repository is not None
            else await asyncio.to_thread(Repository, configuration.database_url)
        )
        adapter = sandbox or build_sandbox(configuration)
        try:
            files = await asyncio.to_thread(
                ArtifactStore,
                configuration.data_dir / "artifacts",
                configuration.max_output_bytes * 3,
            )
            plan_service = PlanService(configuration, store)
            upload_service = UploadService(configuration, store)
            chat_service = ChatService(configuration, store, files)
            discovery_service = discovery or SourceDiscovery()
            application.state.runtime = Runtime(
                settings=configuration,
                repository=store,
                files=files,
                sandbox=adapter,
                plans=plan_service,
                uploads=upload_service,
                chat=chat_service,
                discovery=discovery_service,
                messages=MessageService(
                    configuration,
                    store,
                    chat_service,
                    AutoPlanner(configuration, plan_service, upload_service, discovery_service),
                ),
            )
            yield
        finally:
            try:
                await adapter.shutdown()
            finally:
                if repository is None:
                    await asyncio.to_thread(store.close)

    app = FastAPI(title="OpenResearch Python", version=__version__, lifespan=lifespan)
    install_middleware(app, configuration)
    install_error_handlers(app)
    app.add_middleware(BodyLimitMiddleware, max_bytes=configuration.upload_max_bytes + 65536)
    # CORS 包裹认证层，让认证错误也带上浏览器需要的跨域响应头。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configuration.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-User-Id",
            "X-Session-Id",
            "Last-Event-ID",
        ],
    )
    for router in (
        system.router,
        plans.router,
        events.router,
        uploads.router,
        conversations.router,
        documents.router,
        tasks.router,
        sources.router,
    ):
        app.include_router(router)
    if configuration.web_dir is not None:
        app.mount("/", StaticFiles(directory=configuration.web_dir, html=True), name="workbench")
    return app
