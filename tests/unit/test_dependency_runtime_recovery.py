from unittest.mock import AsyncMock

import pytest
from packaging.requirements import Requirement

from openresearch.agents.code_repair import dependency_constraint
from openresearch.errors import ContractError, DependencyResolutionError
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec, PackageDescription
from openresearch.research.dependency_recovery import DependencyRecovery, DependencyUpdate
from openresearch.research.runtime_recovery import RuntimeRecoveryPolicy, RuntimeResolutionError
from openresearch.sources.dependency_resolution import DependencyResolver


@pytest.mark.parametrize("prefix", ["使用", "用", "使用 "])
def test_human_fixed_package_versions_are_recognized_without_ascii_word_boundaries(prefix):
    assert Requirement(
        dependency_constraint(prefix + "PyYAML==6.0.2运行", "PyYAML")
    ) == Requirement("PyYAML==6.0.2")


def test_source_literals_cannot_replace_the_human_dependency_constraint():
    assert dependency_constraint("执行代码```python\nprint('PyYAML==0')\n```", "PyYAML") is None


@pytest.mark.parametrize("budget", [0, 1])
async def test_runtime_change_preserves_fixed_requests_and_consumes_its_own_budget(
    settings, budget
):
    settings.runtime_profiles = {
        "cpu13": RuntimeProfile(image="python:3.13-slim-bookworm", python="3.13")
    }
    recovery = DependencyRecovery(settings)
    first, alternate = recovery.resolver, DependencyResolver("3.13")
    first.resolve = AsyncMock(
        side_effect=DependencyResolutionError("ACTUAL_REQUIRES_PYTHON_313", 1)
    )
    alternate.resolve = AsyncMock(return_value=EnvironmentSpec(requirements=["example==1"]))
    first.for_python = lambda python: first if python == "3.12" else alternate
    recovery.source.for_python = lambda python: recovery.source
    recovery.source.fetch = AsyncMock(return_value=(EnvironmentLock(python="3.13"), {}))
    recovery.packages.describe = AsyncMock(
        return_value=PackageDescription(
            name="example",
            version="1",
            requires_python=">=3.13",
            metadata_url="https://pypi.org/pypi/example/1/json",
            metadata_sha256="a" * 64,
        )
    )
    update = DependencyUpdate([], ["example==1"], constraints=["example==1"])
    policy = RuntimeRecoveryPolicy("保留example==1", maximum_changes=budget)
    if not budget:
        with pytest.raises(RuntimeResolutionError) as captured:
            await recovery.recover(update, policy)
        assert captured.value.report.attempts[0].exit_code == 1
        assert not captured.value.report.decisions
        recovery.packages.describe.assert_not_awaited()
    else:
        result = await recovery.recover(update, policy)
        assert result.lock.python == "3.13" and result.runtime.profile == "cpu13"
        assert result.requests == ["example==1"]
        assert len(result.preparation.attempts) == 2
        assert result.preparation.decisions[0].accepted
        assert result.preparation.decisions[0].after_requests == ["example==1"]


async def test_checksum_failure_cannot_trigger_runtime_selection(settings):
    recovery = DependencyRecovery(settings)
    recovery.resolver.resolve = AsyncMock(return_value=EnvironmentSpec(requirements=["example==1"]))
    recovery.source.fetch = AsyncMock(side_effect=ContractError("wheel checksum mismatch"))
    recovery.packages.describe = AsyncMock(side_effect=AssertionError("not a runtime problem"))
    with pytest.raises(RuntimeResolutionError) as captured:
        await recovery.recover(
            DependencyUpdate([], ["example==1"]), RuntimeRecoveryPolicy("run", maximum_changes=1)
        )
    assert not captured.value.report.decisions
    assert captured.value.report.attempts[-1].phase == "lock"


async def test_newer_target_grammar_is_deferred_without_rewriting_on_an_older_host(
    settings, monkeypatch
):
    from types import SimpleNamespace

    from openresearch.agents.code_repair import CodeRepairer
    from openresearch.llm.client import LLMClient
    from openresearch.research import code_preparation

    monkeypatch.setattr(code_preparation, "sys", SimpleNamespace(version_info=(3, 12)))
    repairer = CodeRepairer(LLMClient(settings))
    repairer.propose = AsyncMock(
        side_effect=AssertionError("host grammar cannot rewrite target code")
    )
    code = "def identity[T = int](value: T) -> T:\n    return value\nassert identity(61) == 61\n"
    preserved, report = await code_preparation.prepare_code(
        code, "运行原代码", repairer, 2, python="3.13"
    )
    assert report.success and preserved == code
    assert report.syntax_validation == "deferred_to_sandbox"
    assert report.attempts[0].phase == "syntax_pending"
    assert not report.attempts[0].execution_requested and not report.attempts[0].output
    repairer.propose.assert_not_awaited()


def test_runtime_recovery_may_use_an_allowed_version_range_without_overriding_it(settings):
    from openresearch.research.runtime_selection import compatible_runtime

    settings.runtime_profiles = {
        "cpu13": RuntimeProfile(image="python:3.13-slim-bookworm", python="3.13")
    }
    evidence = [{"name": "example", "version": "1", "requires_python": ">=3.13"}]
    selected = compatible_runtime(
        settings, "使用Python>=3.12,<3.14", None, evidence, ["example==1"]
    )
    assert selected.profile == "cpu13"
    assert (
        compatible_runtime(settings, "使用Python>=3.11,<3.13", None, evidence, ["example==1"])
        is None
    )
