import hashlib
import json

import pytest

from openresearch.models.execution import ExecutionLease
from openresearch.models.research import (
    BenchmarkPreflightReport,
    EnvironmentReport,
    ExperimentVariant,
    FilePatch,
    ResearchPatch,
    ResearchReport,
    ResearchRun,
    ResearchSpec,
    RunAttempt,
)
from openresearch.models.sources import EnvironmentLock
from openresearch.research.repairs import RepairPipeline
from openresearch.research.runner import ExperimentInput, ExperimentOutput
from openresearch.research.semantic_claims import semantic_evidence


@pytest.mark.parametrize("mode", ["passed", "blocked", "repaired"])
async def test_changed_code_must_pass_recheck_before_full_retry(mode):
    calls = []
    proposals = []
    request = ExperimentInput(
        lease=ExecutionLease("plan", "task", "attempt", "worker"),
        spec=ResearchSpec(max_repairs=2 if mode == "repaired" else 1),
        variant=ExperimentVariant(name="baseline"),
        files={"openresearch_run.py": b"print(0)"},
        patch=ResearchPatch(),
        lock=EnvironmentLock(),
        dataset=None,
        number=1,
    )

    def result(request, error):
        return ResearchRun(
            variant=request.variant.name,
            arguments=[],
            environment=EnvironmentReport(
                python="3.12", platform="linux", installed={}, container_image="test"
            ),
            attempts=[
                RunAttempt(
                    number=request.number,
                    patch_sha256=hashlib.sha256(
                        request.patch.model_dump_json().encode()
                    ).hexdigest(),
                    patch=request.patch,
                    exit_code=int(bool(error)),
                    stdout="test-only state machine response",
                    stderr=error,
                    error=error,
                )
            ],
        )

    class Runner:
        async def run(self, current):
            calls.append("full")
            return ExperimentOutput(result(current, "TAIL_FAILURE" if len(calls) == 1 else ""), [])

    class Preparer:
        async def propose(self, intent, spec, sources, error):
            proposals.append(error)
            return ResearchPatch(
                files=[
                    FilePatch(
                        path="openresearch_run.py",
                        base_sha256=hashlib.sha256(sources["openresearch_run.py"]).hexdigest(),
                        content=f"print({len(proposals)})",
                    )
                ]
            )

    async def recheck(current):
        calls.append("recheck")
        failed = mode != "passed" and len(proposals) == 1
        return BenchmarkPreflightReport(
            status="failed" if failed else "passed",
            dataset_sha256="a" * 64,
            spec_sha256="b" * 64,
            environment_lock_sha256="c" * 64,
            patch_sha256="d" * 64,
            sample_ids=["0"],
            total_rows=12,
            run=result(current, "RECHECK_FAILURE" if failed else ""),
        )

    outcome = await RepairPipeline(Preparer(), Runner()).run(
        "retain the same benchmark", request.files, request, recheck=recheck
    )
    assert (
        calls
        == {
            "passed": ["full", "recheck", "full"],
            "blocked": ["full", "recheck"],
            "repaired": ["full", "recheck", "recheck", "full"],
        }[mode]
    )
    assert len(outcome.rechecks) == (2 if mode == "repaired" else 1)
    assert bool(outcome.output.run.attempts[-1].error) == (mode == "blocked")
    assert outcome.output.run.attempts[0].stderr == "TAIL_FAILURE"
    assert len(proposals) == (2 if mode == "repaired" else 1)
    if mode == "repaired":
        assert "RECHECK_FAILURE" in proposals[1]
        assert [item.status for item in outcome.rechecks] == ["failed", "passed"]
    report = ResearchReport(
        repository_commit=None,
        runs=[outcome.output.run],
        success=not outcome.output.run.attempts[-1].error,
        preflight_rechecks=list(outcome.rechecks),
    )
    evidence = semantic_evidence(
        {"research_report": report.model_dump_json()}, include_history=True
    )
    history = json.loads(evidence["execution_history"])
    assert len(history) == len(outcome.rechecks) + 1
    if mode != "passed":
        assert "RECHECK_FAILURE" in evidence["execution_history"]
