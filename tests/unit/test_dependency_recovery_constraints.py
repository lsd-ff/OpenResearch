import pytest
from packaging.requirements import Requirement

from openresearch.agents.code_repair import dependency_constraint
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec
from openresearch.research.dependency_recovery import DependencyRecovery


@pytest.mark.parametrize(
    "goal", ["使用 PyYAML==6.0.2", "使用 yaml 版本 6.0.2", "PyYAML version 6.0.2"]
)
def test_explicit_human_version_constraints_are_retained(goal):
    assert Requirement(dependency_constraint(goal, "PyYAML")).specifier.contains("6.0.2")
    assert not Requirement(dependency_constraint(goal, "PyYAML")).specifier.contains("6.0.3")


async def test_a_model_cannot_loosen_a_human_pin_when_resolving_new_wheels(settings, monkeypatch):
    recovery = DependencyRecovery(settings)
    recorded = []

    async def resolve(requests):
        recorded.extend(requests)
        return EnvironmentSpec(requirements=["pyyaml==6.0.2"])

    async def fetch(spec):
        return EnvironmentLock(), {}

    monkeypatch.setattr(recovery.resolver, "resolve", resolve)
    monkeypatch.setattr(recovery.source, "fetch", fetch)
    await recovery.resolve(["PyYAML==6.0.2"], ["PyYAML>=6"], constraints=["PyYAML==6.0.2"])
    assert Requirement(recorded[0]).specifier.contains("6.0.2")
    assert not Requirement(recorded[0]).specifier.contains("6.0.3")
