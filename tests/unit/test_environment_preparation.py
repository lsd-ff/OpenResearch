import json
from unittest.mock import AsyncMock

import pytest

from openresearch.errors import ContractError, DependencyResolutionError, EnvironmentLockError
from openresearch.models.environment_repair import EnvironmentRepairProposal
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec
from openresearch.research.dependency_recovery import DependencyRecovery
from openresearch.research.environment_preparation import EnvironmentPreparation


@pytest.mark.parametrize("phase", ["resolve", "lock"])
async def test_preparation_retries_real_diagnostic_without_changing_user_constraints(
    settings, phase
):
    preparation = EnvironmentPreparation(settings)
    preparation.packages.describe = AsyncMock(
        side_effect=ContractError("test-only metadata unavailable")
    )
    preparation.resolver.resolve = AsyncMock(
        side_effect=[
            DependencyResolutionError("ACTUAL_NO_COMPATIBLE_VERSION", 1),
            EnvironmentSpec(requirements=["example==2", "required==1"]),
        ]
        if phase == "resolve"
        else [EnvironmentSpec(requirements=["example==2", "required==1"])]
    )
    preparation.source.fetch = AsyncMock(
        side_effect=[EnvironmentLockError("ACTUAL_WHEEL_UNSUPPORTED"), (EnvironmentLock(), {})]
        if phase == "lock"
        else None
    )
    calls = []

    async def diagnose(intent, current_phase, error, diagnostic, requests, constraints, locked):
        calls.append(diagnostic)
        assert current_phase == phase and "required==1" in constraints
        return EnvironmentRepairProposal(
            action="dependencies", reason="compatible public version", requirements=["example>=2"]
        )

    preparation.repairer.diagnose = diagnose
    prepared = await preparation.run(
        "use actual dependencies",
        ["example==1", "required==1"],
        ["required==1"],
        1,
        initial_spec=EnvironmentSpec(requirements=["example==1", "required==1"])
        if phase == "lock"
        else None,
        lock_wheels=phase == "lock",
    )
    assert prepared.report.success and len(prepared.report.attempts) == 2
    assert prepared.report.decisions[0].accepted and "ACTUAL_" in calls[0]
    assert prepared.report.attempts[0].exit_code == (1 if phase == "resolve" else None)
    assert prepared.spec.requirements == ["example==2", "required==1"]
    assert "required==1" in prepared.requests


@pytest.mark.parametrize("mode", ["integrity", "budget", "disabled", "unchanged", "invalid"])
async def test_initial_failures_do_not_fabricate_locks_or_bypass_limits(settings, mode):
    preparation = EnvironmentPreparation(settings)
    preparation.packages.describe = AsyncMock(
        side_effect=ContractError("test-only metadata unavailable")
    )
    error = (
        ContractError(
            "combined dependencies exceed configured size limit"
            if mode == "budget"
            else "wheel checksum mismatch"
        )
        if mode in {"integrity", "budget"}
        else (DependencyResolutionError("ACTUAL_CONFLICT", 2))
    )
    preparation.resolver.resolve = AsyncMock(side_effect=error)
    proposal = EnvironmentRepairProposal(
        action="dependencies",
        reason="test-only proposal",
        requirements=[
            "bad @ https://untrusted.invalid/x.whl" if mode == "invalid" else "example==1"
        ],
    )
    preparation.repairer.diagnose = AsyncMock(return_value=proposal)
    prepared = await preparation.run(
        "preserve goal", ["example==1"], [], 0 if mode == "disabled" else 2
    )
    assert not prepared.report.success and prepared.lock is None and prepared.spec is None
    assert len(prepared.report.attempts) == 1
    assert preparation.repairer.diagnose.call_count == (mode in {"unchanged", "invalid"})
    if mode in {"unchanged", "invalid"}:
        assert not prepared.report.decisions[0].accepted and prepared.report.decisions[0].error


async def test_recovery_uses_bounded_128_existing_packages_without_expanding_new_designs(settings):
    recovery = DependencyRecovery(settings)
    current = [f"existing-{index}==1" for index in range(40)]
    recovery.resolver.resolve = AsyncMock(side_effect=AssertionError("not a new 32-root design"))
    recovery.resolver.resolve_recovery = AsyncMock(return_value=EnvironmentSpec())
    recovery.source.fetch = AsyncMock(return_value=(EnvironmentLock(), {}))
    result = await recovery.resolve(current, ["new-package>=1"], constraints=current)
    assert len(result.requests) == 41
    assert recovery.resolver.resolve_recovery.call_count == 1
    assert set(current) <= set(result.requests)


async def test_resolution_failure_retains_actual_uv_exit_and_stderr(settings, tmp_path):
    from openresearch.sources.dependency_resolution import DependencyResolver

    with pytest.raises(DependencyResolutionError) as captured:
        await DependencyResolver()._compile("uv", tmp_path)
    assert captured.value.exit_code != 0 and "requirements.in" in captured.value.diagnostic
    assert "failed" in str(captured.value)


async def test_designer_persists_failed_resolution_report_and_does_not_publish_fake_spec(
    settings, repository, files, monkeypatch
):
    from openresearch.agents.research_design import ResearchDesigner
    from openresearch.models.context import Identity
    from openresearch.models.planning import ExperimentDesign
    from openresearch.models.research import ResearchSpec
    from openresearch.models.submissions import PlanSubmission
    from openresearch.services.plans import PlanService

    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "design a real experiment",
            Identity("alice", "initial-env"),
            workflow="reproduce",
            code="print(1)",
            research=ResearchSpec(max_repairs=0),
            auto_design=True,
        )
    )
    task = next(node for node in plan.nodes if node.type == "research_design")
    designer = ResearchDesigner(settings, files)
    monkeypatch.setattr(designer.preparer, "workspace", lambda _: (None, {}))
    designer.llm.request_typed = AsyncMock(
        return_value=ExperimentDesign(dependencies=["example==1"], rationale="real source proposal")
    )
    designer.dependencies.resolve = AsyncMock(
        side_effect=DependencyResolutionError("REAL_CONFLICT", 3)
    )
    result = await designer.execute(plan, task)
    assert not result.success and "research_spec" not in result.outputs
    report = json.loads(result.outputs["dependency_resolution_report"])
    assert report["attempts"][0]["exit_code"] == 3
    assert report["attempts"][0]["diagnostic"] == "REAL_CONFLICT"


@pytest.mark.parametrize("version", ["6.0.2", "6.0.3"])
async def test_verified_package_release_overrides_stale_model_knowledge_but_not_user_pins(
    settings, version
):
    from openresearch.models.sources import PackageDescription

    preparation = EnvironmentPreparation(settings)
    preparation.packages.describe = AsyncMock(
        return_value=PackageDescription(
            name="pyyaml",
            version="6.0.3",
            requires_python=">=3.8",
            metadata_url="https://pypi.org/pypi/pyyaml/6.0.3/json",
            metadata_sha256="a" * 64,
        )
    )
    preparation.resolver.resolve = AsyncMock(
        side_effect=[
            DependencyResolutionError("ACTUAL_CONFLICT", 1),
            EnvironmentSpec(requirements=["pyyaml==6.0.3"]),
        ]
    )

    async def diagnose(intent, phase, error, diagnostic, requests, constraints, locked):
        assert "https://pypi.org/pypi/pyyaml/6.0.3/json" in diagnostic
        assert '"version": "6.0.3"' in diagnostic
        return EnvironmentRepairProposal(
            action="dependencies", reason="version suggestion", requirements=["PyYAML==" + version]
        )

    preparation.repairer.diagnose = diagnose
    result = await preparation.run("使用 PyYAML==6.0.3", ["PyYAML==99999"], ["PyYAML==6.0.3"], 1)
    assert result.report.success == (version == "6.0.3")
    decision = result.report.decisions[0]
    assert decision.package_evidence[0]["metadata_sha256"] == "a" * 64
    if version == "6.0.2":
        assert not decision.accepted and "fixed version" in decision.error
        assert preparation.resolver.resolve.call_count == 1


@pytest.mark.parametrize("repairs", [0, 1, 2])
async def test_initial_recovery_budget_preserves_every_actual_failed_attempt(settings, repairs):
    preparation = EnvironmentPreparation(settings)
    preparation.packages.describe = AsyncMock(
        side_effect=ContractError("test-only metadata unavailable")
    )
    preparation.resolver.resolve = AsyncMock(
        side_effect=DependencyResolutionError("ACTUAL_UNRESOLVED", 4)
    )
    proposals = []

    async def diagnose(*args):
        proposals.append(args)
        return EnvironmentRepairProposal(
            action="dependencies",
            reason="bounded next version",
            requirements=[f"example=={len(proposals) + 1}"],
        )

    preparation.repairer.diagnose = diagnose
    result = await preparation.run("preserve objective", ["example==1"], [], repairs)
    assert not result.report.success and result.spec is None and result.lock is None
    assert len(proposals) == repairs and len(result.report.attempts) == repairs + 1
    assert all(
        attempt.exit_code == 4 and attempt.diagnostic == "ACTUAL_UNRESOLVED"
        for attempt in result.report.attempts
    )
