import json
from typing import Any

from openresearch.models.documents import PaperCorpus
from openresearch.models.runtime import Plan
from openresearch.storage.files import ArtifactStore


def document_context(plan: Plan, files: ArtifactStore, limit: int = 24000) -> str:
    artifact = plan.artifacts.get("parsed_paper")
    if artifact is None:
        return ""
    corpus = PaperCorpus.model_validate_json(files.read(artifact))
    output: list[dict[str, Any]] = []
    size = 0
    for document in corpus.documents:
        for page in document.pages:
            item = {
                "upload_id": document.source.id,
                "sha256": document.source.sha256,
                "page": page.number,
                "text": page.text[:3000],
                "needs_ocr": page.needs_ocr,
            }
            raw = json.dumps(item, ensure_ascii=False)
            if size + len(raw.encode()) > limit:
                return json.dumps(output, ensure_ascii=False)
            output.append(item)
            size += len(raw.encode())
    return json.dumps(output, ensure_ascii=False)
