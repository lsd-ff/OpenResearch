from typing import Any

from fastapi import APIRouter, Request, Response

from openresearch import __version__
from openresearch.api.dependencies import RuntimeDep

router = APIRouter(tags=["system"])


@router.get("/ping")
def ping() -> dict[str, str]:
    return {"message": "pong"}


@router.get("/api/health")
async def health(runtime: RuntimeDep) -> dict[str, Any]:
    sandbox_ok = await runtime.sandbox.health()
    return {
        "ok": sandbox_ok,
        "version": __version__,
        "backend": {"ok": True},
        "sandbox": {"ok": sandbox_ok, "strategy": runtime.sandbox.backend},
    }


@router.post("/api/auth/session")
def auth_session(request: Request, response: Response, runtime: RuntimeDep) -> dict[str, bool]:
    """已通过令牌校验的本地客户端可为浏览器 SSE 设置会话 Cookie。"""
    expected = runtime.settings.api_token.get_secret_value()
    if expected:
        response.set_cookie(
            "sa_access",
            expected,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            max_age=3600,
        )
    return {"ok": True}
