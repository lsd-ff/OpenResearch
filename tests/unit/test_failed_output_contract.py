import pytest

from openresearch.errors import ContractError
from openresearch.models.execution import validate_output_keys


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
