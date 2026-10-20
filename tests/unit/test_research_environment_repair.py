import json
from dataclasses import replace

import httpx
import pytest
from pydantic import SecretStr

from openresearch.llm.client import LLMClient
from openresearch.models.environment_repair import EnvironmentRepairProposal
from openresearch.models.execution import ExecutionLease
from openresearch.models.research import ExperimentVariant, ResearchPatch, ResearchSpec, RunAttempt
from openresearch.models.sources import EnvironmentLock, LockedWheel
from openresearch.planner.research import repair_tools
from openresearch.research.dependency_recovery import RecoveredEnvironment
from openresearch.research.environment_repair import ResearchEnvironmentRepairer, dependency_failure
from openresearch.research.runner import ExperimentInput


def wheel(name):
    return LockedWheel(
        requirement=name + "==1.0",
        filename=name.replace("-", "_") + "-1.0-py3-none-any.whl",
        sha256="a" * 64,
        size=1,
        url="https://files.pythonhosted.org/test.whl",
    )


def request():
    return ExperimentInput(
        lease=ExecutionLease("plan", "task", "attempt", "worker"),
        spec=ResearchSpec(),
        variant=ExperimentVariant(name="baseline"),
        files={"openresearch_run.py": b"import yaml"},
        patch=ResearchPatch(),
        lock=EnvironmentLock(),
        dataset=None,
        number=1,
        allow_environment_repair=True,
        dependency_constraints=("PyYAML==6.0.3",),
    )


def failure(stderr="ModuleNotFoundError: No module named 'yaml'", phase="execute"):
    return RunAttempt(
        number=1,
        patch_sha256="b" * 64,
        patch=ResearchPatch(),
        exit_code=1,
        stdout="",
        stderr=stderr,
        error=stderr,
        phase=phase,
    )


@pytest.mark.parametrize("mode", ["stdlib", "repository", "nested_repository", "permission"])
async def test_environment_repair_cannot_replace_stdlib_or_repository_code_or_bypass_permissions(
    settings, mode, monkeypatch
):
    current, error = request(), failure()
    if mode == "stdlib":
        error = failure("ModuleNotFoundError: No module named 'json'")
    elif mode == "repository":
        current = replace(current, files={"src/yaml/__init__.py": b""})
    elif mode == "nested_repository":
        current = replace(current, files={"project/package/src/yaml.py": b""})
    else:
        current = replace(current, allow_environment_repair=False)
    repairer = ResearchEnvironmentRepairer(settings)

    async def forbidden(*args):
        raise AssertionError("untrusted environment changes must not reach a model")

    monkeypatch.setattr(repairer, "propose", forbidden)
    assert not dependency_failure(current, error)
    revised = await repairer.revise("original human goal", current, error)
    assert not revised.decision.accepted and "authorized" in revised.decision.error


@pytest.mark.parametrize("mode", ["accepted", "unchanged", "removed"])
async def test_recovery_keeps_user_constraints_and_records_rejected_proposals(
    settings, monkeypatch, mode
):
    current = replace(
        request(),
        lock=EnvironmentLock(wheels=[wheel("old-package")]),
        dependency_requests=("old-package>=1",),
    )
    repairer = ResearchEnvironmentRepairer(settings)
    constraints = []

    async def propose(*args):
        return EnvironmentRepairProposal(
            action="dependencies", reason="actual missing import", requirements=["PyYAML>=6"]
        )

    async def resolve(existing, proposed, missing, limits):
        assert existing == ["old-package>=1"] and missing == "yaml"
        constraints.extend(limits)
        wheels = (
            [wheel("old-package"), wheel("pyyaml")]
            if mode == "accepted"
            else ([wheel("old-package")] if mode == "unchanged" else [wheel("pyyaml")])
        )
        return RecoveredEnvironment([*existing, *proposed], EnvironmentLock(wheels=wheels))

    monkeypatch.setattr(repairer, "propose", propose)
    monkeypatch.setattr(repairer.dependencies, "resolve", resolve)
    revised = await repairer.revise("使用 PyYAML==6.0.3", current, failure())
    assert "PyYAML==6.0.3" in constraints
    assert revised.decision.accepted == (mode == "accepted")
    assert revised.decision.proposal.requirements == ["PyYAML>=6"]
    assert revised.decision.environment_lock == (revised.lock if mode == "accepted" else None)
    if mode != "accepted":
        assert revised.lock == current.lock and revised.decision.error


@pytest.mark.parametrize(
    "instruction,expected",
    [
        ("不要安装依赖", ["research.patch"]),
        ("不要修改代码", ["environment.repair"]),
        ("不要自动修复", []),
    ],
)
def test_research_and_framework_repair_permissions_follow_user_instructions(instruction, expected):
    assert repair_tools(instruction) == expected


async def test_install_diagnosis_sends_actual_error_and_constraints_to_the_model(settings):
    settings.llm_model = "test-only"
    settings.llm_api_key = SecretStr("test-only")

    def provider(request):
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][-1]["content"])
        assert context["phase"] == "install" and "ACTUAL_INSTALL_ERROR" in context["stderr"]
        assert context["constraints"] == ["PyYAML==6.0.3"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "action": "dependencies",
                                    "reason": "controlled diagnostic proposal",
                                    "requirements": ["PyYAML>=6"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    repairer = ResearchEnvironmentRepairer(settings)
    repairer.llm = LLMClient(settings, httpx.MockTransport(provider))
    proposed = await repairer.propose(
        "original goal", request(), failure("ACTUAL_INSTALL_ERROR", "install")
    )
    assert proposed.action == "dependencies" and proposed.requirements == ["PyYAML>=6"]
