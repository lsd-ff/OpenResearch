from typing import Annotated

from fastapi import APIRouter, Query, Response

from openresearch.api.dependencies import IdentityDep, RuntimeDep
from openresearch.models.discovery import SearchRequest, SearchResult
from openresearch.sources.remote_pdf import RemotePdfReader

router = APIRouter(prefix="/api", tags=["sources"])


@router.post("/sources/search")
async def search_sources(
    payload: SearchRequest, runtime: RuntimeDep, identity: IdentityDep
) -> dict[str, list[SearchResult]]:
    return {"results": await runtime.discovery.search(payload)}


@router.get("/pdf-proxy")
async def pdf_proxy(
    url: Annotated[str, Query(max_length=4096)], runtime: RuntimeDep, identity: IdentityDep
) -> Response:
    raw = await RemotePdfReader(runtime.settings).fetch(url)
    return Response(
        raw, media_type="application/pdf", headers={"X-Content-Type-Options": "nosniff"}
    )
