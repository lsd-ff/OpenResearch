from typing import Annotated

import httpx
from fastapi import APIRouter, Query, Response

from openresearch.api.dependencies import IdentityDep, RuntimeDep
from openresearch.errors import ContractError
from openresearch.models.discovery import SearchRequest, SearchResult
from openresearch.sources.discovery import arxiv_pdf_url
from openresearch.sources.http import read_bounded

router = APIRouter(prefix="/api", tags=["sources"])


@router.post("/sources/search")
async def search_sources(
    payload: SearchRequest, runtime: RuntimeDep, identity: IdentityDep
) -> dict[str, list[SearchResult]]:
    return {"results": await runtime.discovery.search(payload)}


@router.get("/pdf-proxy")
async def pdf_proxy(
    url: Annotated[str, Query(max_length=500)], runtime: RuntimeDep, identity: IdentityDep
) -> Response:
    location = arxiv_pdf_url(url)
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        raw = await read_bounded(client, location, runtime.settings.upload_max_bytes)
    if not raw.startswith(b"%PDF-"):
        raise ContractError("remote response is not a PDF")
    return Response(
        raw, media_type="application/pdf", headers={"X-Content-Type-Options": "nosniff"}
    )
