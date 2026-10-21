import hmac
import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from openresearch.config import Settings
from openresearch.errors import (
    ConflictError,
    ContractError,
    ModelError,
    NotFoundError,
    UnsupportedMediaError,
    UploadTooLargeError,
)
from openresearch.models.runtime import new_id

logger = logging.getLogger(__name__)
PUBLIC_PATHS = frozenset({"/ping", "/api/health"})


def authenticated(
    request: Request, settings: Settings, public_paths: frozenset[str] = PUBLIC_PATHS
) -> bool:
    expected = settings.api_token.get_secret_value()
    if not expected or request.method == "OPTIONS" or request.url.path in public_paths:
        return True
    authorization = request.headers.get("Authorization", "")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    token = token or request.cookies.get("sa_access", "")
    return hmac.compare_digest(token.encode(), expected.encode())


def install_middleware(
    app: FastAPI, settings: Settings, *, public_paths: frozenset[str] = PUBLIC_PATHS
) -> None:
    @app.middleware("http")
    async def authenticate_and_trace(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = new_id()
        started = time.monotonic()
        response = (
            await call_next(request)
            if authenticated(request, settings, public_paths)
            else JSONResponse({"error": "authentication required"}, status_code=401)
        )
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            },
        )
        return response


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(UploadTooLargeError)
    async def upload_too_large(_: Request, exc: UploadTooLargeError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=413)

    @app.exception_handler(UnsupportedMediaError)
    async def unsupported_media(_: Request, exc: UnsupportedMediaError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=415)

    @app.exception_handler(ModelError)
    async def unavailable_model(_: Request, exc: ModelError) -> JSONResponse:
        # ModelError 已在供应商边界脱敏，只记录可供运维判断的错误类别。
        logger.warning("model request failed", extra={"error": str(exc)})
        return JSONResponse({"error": str(exc)}, status_code=503)

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(ContractError)
    async def invalid_contract(_: Request, exc: ContractError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=422)
