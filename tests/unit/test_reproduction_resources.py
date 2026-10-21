import pytest

from openresearch.errors import ContractError, ResourceBusyError
from openresearch.models.compute import GpuDevice, ReproductionRequest, SandboxResources
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.planner.code import build_code_plan
from openresearch.research.compute import compute_runtime
from openresearch.research.reproduction_mode import decide_mode, request_from_intent
from openresearch.research.runtime_selection import bind_runtime, task_runtime
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.profiles import profile_fingerprint


def resources(**changes):
    return SandboxResources(
        python="3.12.15",
        platform="Linux",
        cpu_count=16,
        memory_mb=65536,
        workspace_free_mb=102400,
        workspace_storage="volume",
        gpus=[GpuDevice(name="actual-device", memory_mb=6144, free_memory_mb=5000)],
        **changes,
    )


@pytest.mark.parametrize("mode,full", [("auto", False), ("full", True), ("smoke", False)])
def test_full_mode_needs_a_request_as_well_as_observed_resources(settings, mode, full):
    decision = decide_mode(settings, ReproductionRequest(mode=mode), resources())
    assert decision.full_eligible
    assert decision.execution_allowed
    assert (decision.effective_mode == "full") == full


@pytest.mark.parametrize("missing", ["cpu_count", "memory_mb", "workspace_free_mb", "gpus"])
def test_resource_shortage_never_reports_a_full_run_as_eligible(settings, missing):
    probe = resources().model_copy(update={missing: [] if missing == "gpus" else 0})
    decision = decide_mode(settings, ReproductionRequest(mode="full"), probe)
    assert not decision.full_eligible and decision.effective_mode == "smoke"
    assert not decision.execution_allowed
    assert decision.reasons


def test_code_literals_do_not_request_gpu_or_full_training():
    assert request_from_intent('运行代码```python\nprint("GPU 完整复现 BLEU")\n```') is None
    assert request_from_intent("用GPU进行核心架构冒烟测试").device == "gpu"
    assert request_from_intent("完整复现Transformer并计算BLEU").mode == "full"


@pytest.mark.parametrize(
    "intent",
    [
        "运行 full test suite",
        "不用GPU，只验证核心架构",
        "smoke 模式，不要执行 WMT14 完整训练",
        "不要完整训练，不计算BLEU",
        "Do not use GPU; run the full test suite",
    ],
)
def test_negative_and_test_suite_intents_do_not_enable_expensive_execution(intent):
    assert request_from_intent(intent) is None


def test_full_request_can_explicitly_prohibit_gpu():
    request = request_from_intent("不用GPU，完整复现所给CPU程序")
    assert request.mode == "full" and request.device == "cpu"
    assert request_from_intent("use GPU for a full training run").device == "gpu"


def test_default_workspace_and_research_fields_preserve_frozen_old_contracts():
    profile = RuntimeProfile(image="python:3.12-slim", python="3.12")
    assert "workspace_storage" not in profile.model_dump()
    assert "reproduction" not in ResearchSpec().model_dump()
    assert profile_fingerprint(profile) == profile_fingerprint(
        profile.model_copy(update={"workspace_storage": "tmpfs"})
    )
    assert profile_fingerprint(profile) != profile_fingerprint(
        profile.model_copy(update={"workspace_storage": "volume"})
    )


def test_gpu_only_registry_grants_bound_stages_permission_but_does_not_change_old_tasks(settings):
    settings.runtime_profiles = {
        "cuda": RuntimeProfile(image="python:3.12-slim", python="3.12", gpu=True)
    }
    plan = build_code_plan("用GPU运行", "compute", "gpu", settings, "print(1)")
    for task in plan.nodes:
        if task.type in {"runtime_prepare", "code_generate", "code_execute"}:
            assert "runtime.select" in task.contract.allowed_tools
            assert "runtime.probe" in task.contract.allowed_tools
            assert task_runtime(settings, task, plan.user_intent).profile == "cuda"
    old = build_code_plan("运行代码", "compute", "cpu", settings, "print(1)")
    assert all("runtime.probe" not in task.contract.allowed_tools for task in old.nodes)
    assert all(task.inputs.get("runtime") is None for task in old.nodes)
    with pytest.raises(ContractError, match="compute device"):
        compute_runtime(
            settings, "只用CPU", ReproductionRequest(device="cpu"), bind_runtime(settings, "cuda")
        )


async def test_gpu_admission_cannot_escape_to_fallback(sandbox):
    from unittest.mock import AsyncMock

    primary = AsyncMock()
    primary.create.side_effect = ResourceBusyError("busy")
    fallback = AsyncMock()
    with pytest.raises(ResourceBusyError):
        await FallbackSandbox(primary, fallback).create("plan", "execution", "cuda")
    fallback.create.assert_not_called()


def test_full_auto_selection_prefers_an_administrator_configured_sufficient_profile(settings):
    settings.runtime_profiles = {
        "cuda": RuntimeProfile(image="python:3.12-slim", python="3.12", gpu=True),
        "full": RuntimeProfile(
            image="python:3.12-slim",
            python="3.12",
            gpu=True,
            cpus=16,
            memory_mb=65536,
            workspace_storage="volume",
        ),
    }
    full = compute_runtime(settings, "完整复现", ReproductionRequest(mode="full"))
    assert full.profile == "full"
    gpu = compute_runtime(settings, "用GPU运行", ReproductionRequest(device="gpu"))
    assert gpu.profile == "cuda"
