import json

import httpx
import pytest
from pydantic import SecretStr

from openresearch.config import Settings
from openresearch.llm.client import LLMClient
from openresearch.models.claims import FrozenCriterion
from openresearch.models.research import (
    EnvironmentReport,
    FilePatch,
    ResearchPatch,
    ResearchReport,
    ResearchRun,
    RunAttempt,
)
from openresearch.research.semantic_claims import assess_semantic


async def test_source_code_and_failed_attempts_cannot_verify_semantic_claim(
    settings: Settings,
) -> None:
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    patch = ResearchPatch(files=[FilePatch(path="main.py", content="value = 'SOURCE_ONLY_CLAIM'")])
    report = ResearchReport(
        repository_commit=None,
        success=True,
        runs=[
            ResearchRun(
                variant="baseline",
                arguments=[],
                environment=EnvironmentReport(
                    python="3.12", platform="linux", installed={}, container_image="test"
                ),
                attempts=[
                    RunAttempt(
                        number=1,
                        patch_sha256="a" * 64,
                        patch=patch,
                        exit_code=1,
                        stdout="FAILED_ATTEMPT_CLAIM",
                        stderr="error",
                        error="failed",
                    ),
                    RunAttempt(
                        number=2,
                        patch_sha256="b" * 64,
                        patch=patch,
                        exit_code=0,
                        stdout="actual unrelated output",
                        stderr="",
                    ),
                ],
            )
        ],
    )
    criterion = FrozenCriterion(id="criterion-1", description="verify actual behavior")

    def provider(request: httpx.Request) -> httpx.Response:
        assert "SOURCE_ONLY_CLAIM" not in request.content.decode()
        assert "FAILED_ATTEMPT_CLAIM" not in request.content.decode()
        finding = {
            "criterion_id": criterion.id,
            "status": "verified",
            "confidence": 1,
            "evidence_key": "research_report",
            "quote": "SOURCE_ONLY_CLAIM",
            "reason": "incorrectly inferred from source code",
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"findings": [finding]})}}]}
        )

    verdicts = await assess_semantic(
        LLMClient(settings, httpx.MockTransport(provider)),
        [criterion],
        {"research_report": report.model_dump_json()},
    )
    assert verdicts[criterion.id].status == "unverifiable"


@pytest.mark.parametrize(
    "quote,expected",
    [
        ('METRIC {"loss": 0.034}\nALL_CHECKS_PASS', "verified"),
        ("SOURCE_ONLY", "unverifiable"),
        ("EARLIER_ONLY", "unverifiable"),
    ],
)
async def test_decoded_execution_quotes_are_checked_without_accepting_source_or_old_attempts(
    settings, quote, expected
):
    settings.llm_model = "test"
    settings.llm_api_key = SecretStr("test-only")
    patch = ResearchPatch(files=[FilePatch(path="main.py", content="print('SOURCE_ONLY')")])
    report = ResearchReport(
        repository_commit=None,
        success=True,
        runs=[
            ResearchRun(
                variant="baseline",
                arguments=[],
                environment=EnvironmentReport(
                    python="3.12", platform="linux", installed={}, container_image="test"
                ),
                attempts=[
                    RunAttempt(
                        number=1,
                        patch_sha256="a" * 64,
                        patch=patch,
                        exit_code=1,
                        stdout="EARLIER_ONLY",
                        stderr="",
                        error="failed",
                    ),
                    RunAttempt(
                        number=2,
                        patch_sha256="b" * 64,
                        patch=patch,
                        exit_code=0,
                        stdout='METRIC {"loss": 0.034}\nALL_CHECKS_PASS',
                        stderr="",
                    ),
                ],
            )
        ],
    )
    criterion = FrozenCriterion(id="criterion-1", description="Actual loss was printed.")
    if expected == "verified":
        assert quote not in report.model_dump_json(), "JSON encoding escapes raw runtime quotes"

    def provider(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "findings": [
                                        {
                                            "criterion_id": criterion.id,
                                            "status": "verified",
                                            "confidence": 1,
                                            "observed_value": "0.034",
                                            "evidence_key": "research_report",
                                            "quote": quote,
                                            "reason": "Actual execution text",
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    verdicts = await assess_semantic(
        LLMClient(settings, httpx.MockTransport(provider)),
        [criterion],
        {"research_report": report.model_dump_json()},
    )
    assert verdicts[criterion.id].status == expected
