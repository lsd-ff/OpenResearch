import json

import pytest

from openresearch.errors import ContractError
from openresearch.models.context import Identity
from openresearch.models.legacy import DirectExecutionRequest, LegacyEnvironmentState
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec
from openresearch.services.legacy_inputs import legacy_requirements
from openresearch.services.legacy_plans import basic_legacy_plan
from openresearch.services.plans import PlanService


@pytest.mark.parametrize(
    "raw",
    [
        '["numpy>=1.26,<2", "PyYAML==6.0.3"]',
        {"requirements": ["numpy>=1.26,<2", "pyyaml==6.0.3"]},
        "numpy>=1.26,<2, PyYAML==6.0.3",
        "`numpy>=1.26,<2`\nPyYAML==6.0.3",
    ],
)
def test_legacy_dependency_formats_preserve_ranges_and_normalize_names(raw):
    assert legacy_requirements({"dependency_spec": raw}) == ["numpy<2,>=1.26", "pyyaml==6.0.3"]


def test_go_dependency_aliases_preserve_exact_versions_and_detect_conflicts():
    assert legacy_requirements(
        {
            "requirements": ["pytorch==2.5.1", "msgpack_python>=1"],
            "dependency_spec": '["torch==2.5.1", "msgpack>=1"]',
        }
    ) == ["torch==2.5.1", "msgpack>=1"]
    with pytest.raises(ContractError, match="conflicting"):
        legacy_requirements({"requirements": ["PyYAML==6.0.3"], "dependencies": ["PyYAML==6.0.2"]})


@pytest.mark.parametrize(
    "value",
    [
        ["pkg @ https://example.com/wheel.whl"],
        ["pkg[extra]"],
        ["pkg; python_version > '3.11'"],
        ["--index-url https://example.com"],
        ["x" * 257],
    ],
)
def test_legacy_dependency_metadata_cannot_change_source_or_build_policy(value):
    with pytest.raises(ContractError):
        legacy_requirements({"dependency_spec": value})


@pytest.mark.parametrize(
    "alias",
    ["prepare_runtime", "env_setup", "prepare_environment", "setup_runtime", "test_environment"],
)
def test_environment_aliases_create_probe_stage_with_server_budgets(settings, repository, alias):
    plan = basic_legacy_plan(
        DirectExecutionRequest(
            task_id="go-client-id",
            task_type=alias,
            assigned_to="sandbox_agent",
            task_description="准备环境",
            inputs={"budget": {"max_task_attempts": 99999}},
        ),
        Identity("alice", "legacy"),
        PlanService(settings, repository),
    )
    assert len(plan.nodes) == 1 and plan.nodes[0].type == "legacy_environment"
    assert plan.nodes[0].inputs["mode"] == "prepare"
    assert plan.budget.max_task_attempts == settings.max_task_attempts
    assert plan.legacy.requested_type == alias and plan.id != "go-client-id"


def test_gpu_code_generation_does_not_require_or_allocate_a_gpu(settings, repository):
    plan = basic_legacy_plan(
        DirectExecutionRequest(
            task_type="generate_code",
            task_description="只生成 GPU 代码，不执行程序",
            inputs={"code": "import torch\nprint(torch.cuda.is_available())\n"},
        ),
        Identity("alice", "generate"),
        PlanService(settings, repository),
    )
    assert [task.type for task in plan.nodes] == ["code_generate"]
    assert "sandbox.python" not in plan.nodes[0].contract.allowed_tools
    assert plan.legacy is not None


@pytest.mark.parametrize(
    "change",
    [
        {"phase": "installed"},
        {"phase": "probe", "spec": {"requirements": ["pkg==1"]}},
        {"spec": {"requirements": ["pkg==1"]}},
    ],
)
def test_preparation_receipt_cannot_claim_unlocked_or_uninstalled_packages(change):
    raw = {"spec": EnvironmentSpec().model_dump(), "lock": EnvironmentLock().model_dump(), **change}
    with pytest.raises(ValueError):
        LegacyEnvironmentState.model_validate(raw)


def test_old_native_plans_do_not_gain_legacy_metadata(settings, repository):
    from openresearch.models.submissions import PlanSubmission

    plan = PlanService(settings, repository).build(
        PlanSubmission("smoke", Identity("alice", "native"))
    )
    assert "legacy" not in json.loads(plan.model_dump_json())
