import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from openresearch.api.body_limit import BodyLimitMiddleware
from openresearch.api.middleware import install_error_handlers, install_middleware
from openresearch.config import Settings
from openresearch.models.runtime import ContractModel, ExecutionResult, new_id
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.commands import command_program
from openresearch.sandbox.docker import DockerSandbox
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.input_bundle import bundle_limit, unpack_bundle
from openresearch.sandbox.opensandbox import OpenSandbox
from openresearch.sandbox.service_stream import execution_stream


class CreateSandbox(ContractModel):
    image: str = ""
    mount_path: str = Field(default="", max_length=0)
    plan_id: str = Field(default_factory=new_id, pattern=r"^[a-f0-9]{32}$")
    execution_id: str = Field(default_factory=new_id, pattern=r"^[a-f0-9]{32}$")


class PythonExecution(ContractModel):
    code: str = Field(min_length=1, max_length=131072)


class CommandExecution(ContractModel):
    cmd: list[str] = Field(min_length=1, max_length=128)


def create_sandbox_app(settings: Settings | None = None, adapter: Sandbox | None = None) -> FastAPI:
    configuration = settings or Settings()
    engine = adapter or FallbackSandbox(
        DockerSandbox(configuration),
        OpenSandbox(configuration) if configuration.sandbox_fallback_enabled else None,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await engine.shutdown()

    app = FastAPI(title="OpenResearch Sandbox Service", lifespan=lifespan)
    install_error_handlers(app)
    install_middleware(
        app, configuration.model_copy(update={"api_token": configuration.sandbox_token})
    )
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=1024 * 1024,
        path_limits={r"/api/v1/sandboxes/[^/]+/files": bundle_limit(configuration)},
    )

    async def owned(identifier: str) -> None:
        if identifier not in {item.id for item in await engine.inventory()}:
            raise HTTPException(404, "sandbox not found in this runtime namespace")

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        return {"ok": await engine.health(), "backend": engine.backend}

    @app.post("/api/v1/sandboxes", status_code=201)
    async def create(payload: CreateSandbox) -> dict[str, str]:
        if payload.image and payload.image != configuration.sandbox_image:
            raise HTTPException(422, "sandbox image is selected by the service configuration")
        creation = asyncio.create_task(engine.create(payload.plan_id, payload.execution_id))
        try:
            identifier = await asyncio.shield(creation)
        except asyncio.CancelledError:
            identifier = await creation
            await asyncio.shield(engine.close(identifier))
            raise
        return {"sandbox_id": identifier}

    @app.get("/api/v1/sandboxes")
    async def inventory() -> dict[str, Any]:
        return {"sandboxes": [asdict(item) for item in await engine.inventory()]}

    @app.get("/api/v1/sandboxes/{identifier}/image")
    async def image(identifier: str) -> dict[str, str | None]:
        await owned(identifier)
        return {"image_identity": await engine.image_identity(identifier)}

    @app.put("/api/v1/sandboxes/{identifier}/files")
    async def put_files(identifier: str, request: Request) -> dict[str, int]:
        await owned(identifier)
        raw = await request.body()
        files = await asyncio.to_thread(unpack_bundle, raw, configuration)
        await engine.put_files(identifier, files)
        return {"files": len(files), "bytes": sum(map(len, files.values()))}

    @app.post("/api/v1/sandboxes/{identifier}/python/stream")
    async def execute(identifier: str, payload: PythonExecution) -> StreamingResponse:
        await owned(identifier)
        return StreamingResponse(
            execution_stream(engine, identifier, payload.code, configuration),
            media_type="application/x-ndjson",
        )

    @app.post("/api/v1/sandboxes/{identifier}/commands")
    async def command(identifier: str, payload: CommandExecution) -> ExecutionResult:
        return await completed_execution(identifier, command_program(payload.cmd))

    @app.post("/api/v1/sandboxes/{identifier}/python")
    async def python(identifier: str, payload: PythonExecution) -> ExecutionResult:
        return await completed_execution(identifier, payload.code)

    async def completed_execution(identifier: str, code: str) -> ExecutionResult:
        await owned(identifier)
        async for record in execution_stream(engine, identifier, code, configuration):
            event = json.loads(record)
            if event["type"] == "final":
                return ExecutionResult.model_validate(event["response"])
        raise HTTPException(502, "sandbox execution failed")

    @app.post("/api/v1/sandboxes/{identifier}/commands/stream")
    async def stream_command(identifier: str, payload: CommandExecution) -> StreamingResponse:
        await owned(identifier)
        return StreamingResponse(
            execution_stream(engine, identifier, command_program(payload.cmd), configuration),
            media_type="application/x-ndjson",
        )

    @app.delete("/api/v1/sandboxes/{identifier}")
    async def close(identifier: str) -> dict[str, bool]:
        await owned(identifier)
        await engine.close(identifier)
        return {"ok": True}

    return app
