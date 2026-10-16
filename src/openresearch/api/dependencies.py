import re
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, Response

from openresearch.config import Settings
from openresearch.models.context import Identity
from openresearch.models.runtime import new_id
from openresearch.sandbox.base import Sandbox
from openresearch.services.chat import ChatService
from openresearch.services.messages import MessageService
from openresearch.services.plans import PlanService
from openresearch.services.uploads import UploadService
from openresearch.sources.discovery import SourceDiscovery
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


@dataclass
class Runtime:
    settings: Settings
    repository: Repository
    files: ArtifactStore
    sandbox: Sandbox
    plans: PlanService
    uploads: UploadService
    chat: ChatService
    discovery: SourceDiscovery
    messages: MessageService


def get_runtime(request: Request) -> Runtime:
    return cast(Runtime, request.app.state.runtime)


def resolve_identity(request: Request, response: Response, header: str, cookie: str) -> str:
    value = request.headers.get(header) or request.cookies.get(cookie) or new_id()
    if not re.fullmatch(r"[A-Za-z0-9@._-]{1,128}", value):
        raise HTTPException(400, "invalid session identity")
    response.set_cookie(
        cookie,
        value,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=604800,
    )
    return value


def get_identity(request: Request, response: Response) -> Identity:
    """本地可信客户端的身份标识；公网部署应替换为认证后的主体。"""
    return Identity(
        resolve_identity(request, response, "X-User-Id", "sa_uid"),
        resolve_identity(request, response, "X-Session-Id", "sa_sid"),
    )


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]
IdentityDep = Annotated[Identity, Depends(get_identity)]
