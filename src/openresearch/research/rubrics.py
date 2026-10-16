import hashlib
import json

from openresearch.errors import ContractError
from openresearch.models.claims import FrozenClaim, FrozenCriterion, FrozenRubric, RubricDraft
from openresearch.models.documents import PaperCorpus


def rubric_digest(rubric: FrozenRubric) -> str:
    raw = json.dumps(
        rubric.model_dump(exclude={"sha256"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def freeze_rubric(draft: RubricDraft, corpus: PaperCorpus | None) -> FrozenRubric:
    documents = {document.source.id: document for document in corpus.documents} if corpus else {}
    claims = []
    for index, claim in enumerate(draft.claims, 1):
        identifier = f"claim-{index:03d}"
        locator, digest = "user specification", None
        if claim.source:
            document = documents.get(claim.source.upload_id)
            if document is None or claim.source.page > len(document.pages):
                raise ContractError("rubric cites an unknown document page")
            page = document.pages[claim.source.page - 1]
            if not claim.source.quote.strip() or claim.source.quote not in page.text:
                raise ContractError("rubric source quote does not match the actual page")
            locator = f"{claim.source.upload_id}:page:{claim.source.page}"
            digest = document.source.sha256
        claims.append(
            FrozenClaim(
                id=identifier,
                title=claim.title,
                statement=claim.statement,
                source_locator=locator,
                source_sha256=digest,
                claim_type="quantitative"
                if all(item.metric_name for item in claim.criteria)
                else "semantic",
                criteria=[
                    FrozenCriterion(
                        **criterion.model_dump(), id=f"{identifier}.criterion-{number:02d}"
                    )
                    for number, criterion in enumerate(claim.criteria, 1)
                ],
            )
        )
    rubric = FrozenRubric(
        paper_title=draft.paper_title,
        source_artifact="parsed_paper" if corpus else "request",
        claims=claims,
        sha256="",
    )
    rubric.sha256 = rubric_digest(rubric)
    return rubric


def verify_rubric(rubric: FrozenRubric) -> None:
    if rubric.sha256 != rubric_digest(rubric):
        raise ContractError("frozen rubric checksum mismatch")
    for index, claim in enumerate(rubric.claims, 1):
        expected = f"claim-{index:03d}"
        if claim.id != expected or any(
            item.id != f"{expected}.criterion-{number:02d}"
            for number, item in enumerate(claim.criteria, 1)
        ):
            raise ContractError("frozen rubric IDs are not canonical")
