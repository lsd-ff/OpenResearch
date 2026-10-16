import pytest

from openresearch.errors import ContractError
from openresearch.models.claims import (
    ClaimDraft,
    CriterionDraft,
    FrozenCriterion,
    RubricDraft,
    SemanticAssessment,
    SemanticFinding,
)
from openresearch.research.rubrics import freeze_rubric, rubric_digest, verify_rubric
from openresearch.research.semantic_claims import validate_semantic


def test_rubric_hash_and_canonical_ids_cannot_drift_after_freeze() -> None:
    draft = RubricDraft(
        claims=[
            ClaimDraft(
                title="accuracy",
                statement="accuracy >= 0.9",
                criteria=[
                    CriterionDraft(
                        description="accuracy", metric_name="accuracy", expected_value=0.9
                    )
                ],
            )
        ]
    )
    rubric = freeze_rubric(draft, None)
    assert rubric.claims[0].id == "claim-001"
    verify_rubric(rubric)
    verify_rubric(type(rubric).model_validate_json(rubric.model_dump_json()))
    rubric.claims[0].criteria[0].expected_value = 0.8
    with pytest.raises(ContractError, match="checksum"):
        verify_rubric(rubric)
    rubric.claims[0].id = "invented"
    rubric.sha256 = rubric_digest(rubric)
    with pytest.raises(ContractError, match="canonical"):
        verify_rubric(rubric)


@pytest.mark.parametrize("failure", ["unknown", "duplicate", "quote", "source_only"])
def test_semantic_assessment_rejects_forged_or_incomplete_evidence(failure) -> None:
    criterion = FrozenCriterion(id="claim-001.criterion-01", description="real behavior")
    finding = SemanticFinding(
        criterion_id=criterion.id,
        status="verified",
        confidence=0.9,
        evidence_key="research_report",
        quote="actual output",
        reason="observed",
    )
    evidence = {"research_report": "actual output", "parsed_paper": "actual output"}
    if failure == "unknown":
        finding.criterion_id = "invented"
    if failure == "quote":
        finding.quote = "invented output"
    if failure == "source_only":
        finding.evidence_key = "parsed_paper"
    findings = [finding, finding] if failure == "duplicate" else [finding]
    with pytest.raises(ContractError):
        validate_semantic([criterion], SemanticAssessment(findings=findings), evidence)
