from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from openresearch.errors import ContractError, DependencyResolutionError
from openresearch.models.comparison import FrameworkTarget
from openresearch.models.environment_repair import EnvironmentRepairProposal
from openresearch.models.sources import EnvironmentSpec
from openresearch.research.dependency_recovery import merge_requests, protected_requests
from openresearch.research.environment_preparation import EnvironmentPreparation
from openresearch.sources.frameworks import FrameworkSources


def test_replacement_removes_only_the_declared_obsolete_request():
    requests = merge_requests(
        ["old-yaml==1", "required==2"], ["PyYAML>=6"], ["required==2"], ["old-yaml"]
    )
    assert set(requests) == {"required==2", "PyYAML>=6"}


@pytest.mark.parametrize("mode", ["user", "unknown", "same_package"])
def test_removal_cannot_drop_constraints_or_unrelated_packages(mode):
    with pytest.raises(ContractError):
        merge_requests(
            ["pyyaml==6.0.3"],
            ["pyyaml>=6"] if mode == "same_package" else [],
            ["pyyaml==6.0.3"] if mode == "user" else [],
            ["unknown" if mode == "unknown" else "pyyaml"],
        )


def test_user_requested_library_aliases_are_protected_but_stdlib_is_not_a_pypi_request():
    assert protected_requests("继续使用 yaml，允许替换错误依赖", ["pyyaml>=6", "other==1"]) == [
        "pyyaml"
    ]
    assert protected_requests("使用 shutil 复制文件", ["shutil==0"]) == []
    assert protected_requests('查看代码\n```python\nprint("使用PyYAML")\n```', ["pyyaml>=6"]) == []


@pytest.mark.parametrize(
    "removal", ["package==1", "package @ https://example.com/a.whl", "package[extra]"]
)
def test_removed_dependencies_cannot_include_options_or_urls(removal):
    with pytest.raises((ValidationError, ValueError)):
        EnvironmentRepairProposal(
            action="dependencies", reason="invalid removal", remove_requirements=[removal]
        )


def test_additions_and_removals_share_the_eight_change_budget():
    with pytest.raises(ValidationError, match="eight"):
        EnvironmentRepairProposal(
            action="dependencies",
            reason="too many changes",
            requirements=[f"new-{index}" for index in range(5)],
            remove_requirements=[f"old-{index}" for index in range(4)],
        )


async def test_stdlib_request_can_be_removed_without_changing_the_source_or_goal(settings):
    preparation = EnvironmentPreparation(settings)
    preparation.resolver.resolve = AsyncMock(return_value=EnvironmentSpec())
    preparation.packages.describe = AsyncMock(
        side_effect=ContractError("test-only metadata unavailable")
    )
    preparation.repairer.diagnose = AsyncMock(
        return_value=EnvironmentRepairProposal(
            action="dependencies",
            reason="shutil belongs to the Python standard library",
            remove_requirements=["shutil"],
        )
    )
    result = await preparation.run("用 shutil 复制实际文件，保留原代码", ["shutil==0"], [], 1)
    assert result.report.success and result.requests == []
    assert result.report.decisions[0].accepted and len(result.report.attempts) == 2
    assert result.report.attempts[0].exit_code is None and result.spec.requirements == []
    assert "standard library" in result.report.attempts[0].diagnostic


@pytest.mark.parametrize("repairs", [0, 1])
async def test_framework_initial_failure_never_publishes_fake_source_or_changes_user_pin(
    settings, repairs
):
    sources = FrameworkSources(settings)
    sources.resolver.resolve = AsyncMock(
        side_effect=DependencyResolutionError("actual unavailable pin", 1)
    )
    sources.packages.describe = AsyncMock(
        side_effect=ContractError("test-only metadata unavailable")
    )
    sources.preparation.repairer.diagnose = AsyncMock(
        return_value=EnvironmentRepairProposal(
            action="dependencies",
            reason="forbidden alternate version",
            requirements=["pyyaml==6.0.3"],
        )
    )
    prepared = await sources.prepare(
        FrameworkTarget(name="PyYAML", requirements=["pyyaml==99999"], import_roots=["yaml"]),
        "preserve framework",
        repairs,
    )
    assert not prepared.report.success
    assert prepared.source is None and prepared.lock is None and prepared.environment is None
    if repairs:
        assert (
            not prepared.report.decisions[0].accepted
            and "fixed version" in prepared.report.decisions[0].error
        )
    else:
        sources.preparation.repairer.diagnose.assert_not_called()
