import pytest

from openresearch.errors import ContractError
from openresearch.models.execution import validate_output_keys
from openresearch.models.runtime import Plan, Task, TaskContract


def test_failed_task_can_preserve_diagnostics_without_pretending_to_produce_files():
    validate_output_keys(["report", "files"], ["report"], False)


@pytest.mark.parametrize(
    ("returned", "success"),
    [
        (["report"], True),
        (["report", "secret"], False),
        (["report", "report"], False),
    ],
)
def test_success_is_complete_and_failures_cannot_bypass_output_permissions(returned, success):
    with pytest.raises(ContractError):
        validate_output_keys(["report", "files"], returned, success)


def test_optional_execution_receipts_still_require_diagnostics_and_reject_extra_outputs():
    validate_output_keys(["report", "files"], ["report"], True, ["files"])
    for returned in [[], ["files"], ["report", "secret"]]:
        with pytest.raises(ContractError):
            validate_output_keys(["report", "files"], returned, True, ["files"])


def test_ordinary_task_cannot_exempt_missing_outputs_with_gap_contract():
    task = Task(
        name="ordinary",
        type="code_execute",
        assigned_to="coder_agent",
        output_artifacts=["research_report"],
        contract=TaskContract(
            output_artifacts=["research_report"], optional_output_artifacts=["research_report"]
        ),
    )
    with pytest.raises(ValueError, match="restricted to result-gap"):
        Plan(user_intent="run", owner_id="alice", session_id="session", nodes=[task])
