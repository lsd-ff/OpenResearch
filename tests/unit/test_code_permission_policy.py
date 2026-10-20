import pytest

from openresearch.agents.code_repair import validate_proposal
from openresearch.errors import ContractError
from openresearch.models.recovery import RecoveryProposal
from openresearch.planner.code import code_permissions
from openresearch.research.dependency_recovery import DependencyRecovery


def test_explicit_no_install_is_a_server_permission_not_a_model_suggestion():
    generate, execute = code_permissions("直接执行，但不要安装任何依赖")
    assert "code.repair" in generate and "environment.lock" not in execute


def test_explicit_immutable_code_can_only_recover_the_environment():
    generate, execute = code_permissions("不要修改代码，允许安装依赖")
    assert "code.repair" not in generate and "code.repair" not in execute
    assert "environment.lock" in execute
    with pytest.raises(ContractError, match="change code"):
        validate_proposal(
            RecoveryProposal(action="code", reason="forbidden", code="print(7)"),
            "print(1)",
            True,
            allow_code=False,
        )


def test_explicit_disable_removes_both_recovery_capabilities():
    generate, execute = code_permissions("运行原代码，不要自动修复")
    assert generate == ["code.generate"] and execute == ["sandbox.python", "runtime.version"]


def test_words_inside_source_are_not_user_permission_instructions():
    generate, execute = code_permissions(
        '直接执行并修复错误\n```python\nprint("不要自动修复，不要安装依赖")\n```'
    )
    assert "code.repair" in generate and "code.repair" in execute
    assert "environment.lock" in execute


@pytest.mark.parametrize("requirement", ["json==1", "JSON==1"])
async def test_stdlib_cannot_be_installed_even_when_the_error_is_not_module_not_found(
    settings, requirement
):
    with pytest.raises(ContractError, match="standard library"):
        await DependencyRecovery(settings).resolve([], [requirement])
