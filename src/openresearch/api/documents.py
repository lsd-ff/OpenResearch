from typing import Annotated, Any

from fastapi import APIRouter, Path

from openresearch.api.dependencies import IdentityDep, Runtime, RuntimeDep
from openresearch.errors import ConflictError, NotFoundError
from openresearch.models.documents import PaperCorpus

router = APIRouter(prefix="/api/plans", tags=["documents"])


def read_corpus(runtime: Runtime, plan_id: str, owner_id: str) -> PaperCorpus:
    plan = runtime.repository.get_plan(plan_id, owner_id)
    artifact = plan.artifacts.get("parsed_paper")
    if artifact is None:
        raise ConflictError("document extraction has not completed")
    return PaperCorpus.model_validate_json(runtime.files.read(artifact))


@router.get("/{plan_id}/documents")
def list_documents(plan_id: str, runtime: RuntimeDep, identity: IdentityDep) -> dict[str, Any]:
    corpus = read_corpus(runtime, plan_id, identity.user_id)
    return {
        "documents": [
            {
                "source": document.source.model_dump(),
                "title": document.title,
                "status": document.status,
                "page_count": len(document.pages),
                "pages": [
                    {
                        "number": page.number,
                        "needs_ocr": page.needs_ocr,
                        "content_url": (
                            f"/api/plans/{plan_id}/documents/"
                            f"{document.source.id}/pages/{page.number}"
                        ),
                    }
                    for page in document.pages
                ],
            }
            for document in corpus.documents
        ]
    }


@router.get("/{plan_id}/documents/{upload_id}/pages/{number}")
def get_page(
    plan_id: str,
    upload_id: str,
    number: Annotated[int, Path(ge=1)],
    runtime: RuntimeDep,
    identity: IdentityDep,
) -> dict[str, Any]:
    corpus = read_corpus(runtime, plan_id, identity.user_id)
    document = next((item for item in corpus.documents if item.source.id == upload_id), None)
    if document is None or number > len(document.pages):
        raise NotFoundError("document page not found")
    return {"source": document.source.model_dump(), **document.pages[number - 1].model_dump()}
