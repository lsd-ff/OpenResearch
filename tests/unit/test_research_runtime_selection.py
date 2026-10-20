import json
from unittest.mock import AsyncMock

import pytest

from openresearch.errors import ContractError, DependencyResolutionError
from openresearch.models.environment_repair import EnvironmentRepairProposal
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.models.sources import PackageDescription
from openresearch.research.environment_preparation import EnvironmentPreparation
from openresearch.research.runtime_selection import (
    bind_runtime,
    compatible_runtime,
    runtime_for_intent,
    runtime_python,
)


def registry(settings):
    settings.runtime_profiles = {
        "cpu11": RuntimeProfile(image="python:3.11-slim-bookworm", python="3.11"),
        "cpu13": RuntimeProfile(image="python:3.13-slim-bookworm", python="3.13"),
    }


def test_absent_runtime_keeps_old_research_json_and_frozen_preflight_hashes():
    spec = ResearchSpec()
    raw = spec.model_dump_json()
    assert "runtime" not in json.loads(raw)
    assert ResearchSpec.model_validate_json(raw).model_dump_json() == raw


@pytest.mark.parametrize("version", ["3.11", "3.13"])
def test_explicit_python_request_selects_an_operator_registered_cpu_profile(settings, version):
    registry(settings)
    binding = runtime_for_intent(settings, "请用 Python " + version + " 运行测试")
    assert binding.profile == "cpu" + version.replace("3.", "")
    assert runtime_python(settings, binding) == version


def test_runtime_binding_rejects_configuration_changes_and_silent_cpu_fallback(settings):
    registry(settings)
    binding = bind_runtime(settings, "cpu13")
    settings.runtime_profiles["cpu13"] = RuntimeProfile(image="python:3.12-slim", python="3.12")
    with pytest.raises(ContractError, match="changed"):
        runtime_python(settings, binding)
    with pytest.raises(ContractError, match="configured"):
        runtime_for_intent(settings, "use Python 3.11", bind_runtime(settings, "cpu13"))
    with pytest.raises(ContractError, match="registered"):
        runtime_for_intent(settings, "use Python 3.10")


def test_quoted_code_cannot_select_a_runtime_or_request_gpu(settings):
    registry(settings)
    assert runtime_for_intent(settings, '运行测试 ```python\nprint("Python 3.13")\n```') is None
    settings.runtime_profiles["gpu13"] = RuntimeProfile(
        image="python:3.13-slim-bookworm", python="3.13", gpu=True
    )
    with pytest.raises(ContractError, match=r"GPU.*admission"):
        runtime_for_intent(settings, "运行程序", bind_runtime(settings, "gpu13"))


@pytest.mark.parametrize("allowed", [False, True])
async def test_actual_python_incompatibility_can_switch_only_with_runtime_permission(
    settings, allowed
):
    registry(settings)
    preparation = EnvironmentPreparation(settings)
    preparation.resolver.resolve = AsyncMock(
        side_effect=DependencyResolutionError("Requires-Python >=3.13 is incompatible", 1)
    )
    preparation.packages.describe = AsyncMock(
        return_value=PackageDescription(
            name="actual-package",
            version="1",
            requires_python=">=3.13",
            metadata_url="https://pypi.org/pypi/actual-package/1/json",
            metadata_sha256="a" * 64,
        )
    )
    preparation.repairer.diagnose = AsyncMock(
        return_value=EnvironmentRepairProposal(action="stop", reason="keep fixed package")
    )
    from openresearch.sources.dependency_resolution import DependencyResolver

    alternate = DependencyResolver("3.13")
    alternate.resolve = AsyncMock(side_effect=DependencyResolutionError("TARGET_TEST_ONLY_STOP", 2))
    current = preparation.resolver
    preparation.resolver.for_python = lambda python: current if python == "3.12" else alternate
    result = await preparation.run(
        "保留 actual-package==1，不允许改包",
        ["actual-package==1"],
        ["actual-package==1"],
        1,
        allow_runtime_select=allowed,
    )
    assert not result.report.success
    assert result.report.attempts[0].exit_code == 1
    if allowed:
        # 新目标走新的解析器，保留原固定包；本测试不联网，以明确异常终止第二轮。
        assert result.report.decisions[0].runtime_after.profile == "cpu13"
    else:
        assert not result.report.decisions or not result.report.decisions[-1].accepted


@pytest.mark.parametrize(
    "intent",
    [
        "请勿升级Python",
        "不要自动切换Python",
        "保持Python版本",
        "use Python==3.12",
        "do not change Python",
    ],
)
def test_user_runtime_constraints_are_not_overridden_by_public_package_metadata(settings, intent):
    registry(settings)
    evidence = [{"name": "example", "version": "1", "requires_python": ">=3.13"}]
    assert compatible_runtime(settings, intent, None, evidence, ["example==1"]) is None


def test_unrelated_latest_release_cannot_justify_switching_a_pinned_environment(settings):
    registry(settings)
    evidence = [{"name": "example", "version": "2", "requires_python": ">=3.13"}]
    assert (
        compatible_runtime(settings, "run the real package", None, evidence, ["example<2"]) is None
    )


@pytest.mark.parametrize("constraint", ["3.13", ">=3.13,<3.14", "==3.13.*"])
def test_chinese_python_requests_and_version_ranges_select_the_same_target(settings, constraint):
    registry(settings)
    assert runtime_for_intent(settings, "用Python" + constraint + "运行").profile == "cpu13"


async def test_legacy_task_without_runtime_permission_cannot_adopt_a_named_runtime(
    settings, repository
):
    from openresearch.agents.environment import EnvironmentAgent
    from openresearch.models.context import Identity
    from openresearch.models.submissions import PlanSubmission
    from openresearch.services.plans import PlanService

    registry(settings)
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "用Python3.13运行",
            Identity("a", "b"),
            code="print(1)",
            workflow="reproduce",
            research=ResearchSpec(),
        )
    )
    task = next(node for node in plan.nodes if node.type == "environment_lock")
    task.contract.allowed_tools.remove("runtime.select")
    with pytest.raises(ContractError, match="not authorized"):
        await EnvironmentAgent(settings).execute(task)


async def test_runtime_evidence_uses_the_failed_pin_before_an_unversioned_constraint(settings):
    preparation = EnvironmentPreparation(settings)
    preparation.packages.describe = AsyncMock(
        return_value=PackageDescription(
            name="example",
            version="1",
            requires_python=">=3.13",
            metadata_url="https://pypi.org/pypi/example/1/json",
            metadata_sha256="b" * 64,
        )
    )
    await preparation._package_evidence(["example==1"], ["example"])
    preparation.packages.describe.assert_awaited_once_with("example", "1")


def test_preflight_cannot_be_reused_after_a_runtime_binding_changes(settings):
    from dataclasses import replace

    from openresearch.models.execution import ExecutionLease
    from openresearch.models.interpreters import InterpreterIdentity
    from openresearch.models.research import (
        BenchmarkPreflightReport,
        ExperimentVariant,
        ResearchPatch,
        ResearchRun,
    )
    from openresearch.models.sources import EnvironmentLock
    from openresearch.research.benchmark import Dataset
    from openresearch.research.preflight import checksum, verify_preflight
    from openresearch.research.runner import ExperimentInput

    registry(settings)
    spec = ResearchSpec(runtime=bind_runtime(settings, "cpu13"))
    request = ExperimentInput(
        lease=ExecutionLease("a" * 32, "b" * 32, "c" * 32, "test"),
        spec=spec,
        variant=ExperimentVariant(name="baseline"),
        files={},
        patch=ResearchPatch(),
        lock=EnvironmentLock(
            python="3.13",
            interpreter=InterpreterIdentity(
                python_full_version="3.13.16",
                implementation="CPython",
                system="Linux",
                machine="x86_64",
                libc="glibc",
                libc_version="2.36",
            ),
        ),
        dataset=Dataset(
            inputs=[{"id": "1", "inputs": {"x": 1}}], targets={"1": "yes"}, sha256="d" * 64
        ),
        number=1,
    )
    checked = BenchmarkPreflightReport(
        status="passed",
        dataset_sha256=request.dataset.sha256,
        spec_sha256=checksum(spec.model_dump_json()),
        environment_lock_sha256=checksum(request.lock.model_dump_json()),
        patch_sha256=checksum(request.patch.model_dump_json()),
        sample_ids=["1"],
        total_rows=1,
        run=ResearchRun(variant="preflight", arguments=[], attempts=[]),
    )
    verify_preflight(checked, request)
    patch_changed = request.lock.model_copy(
        update={
            "interpreter": request.lock.interpreter.model_copy(
                update={"python_full_version": "3.13.17"}
            )
        }
    )
    with pytest.raises(ContractError, match="preflight does not match"):
        verify_preflight(checked, replace(request, lock=patch_changed))
    settings.runtime_profiles["another13"] = settings.runtime_profiles["cpu13"].model_copy(
        update={"cpus": 2}
    )
    changed = spec.model_copy(update={"runtime": bind_runtime(settings, "another13")})
    with pytest.raises(ContractError, match="preflight does not match"):
        verify_preflight(checked, replace(request, spec=changed))
