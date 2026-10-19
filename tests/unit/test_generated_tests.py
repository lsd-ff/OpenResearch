import pytest

from openresearch.errors import ContractError
from openresearch.models.research import ResearchSpec
from openresearch.research.patches import validate_generated_tests


@pytest.mark.parametrize("code", ["print('tests passed')", "pass", "assert True"])
def test_print_only_or_constant_checks_cannot_satisfy_requested_tests(code):
    with pytest.raises(ContractError, match="no executable"):
        validate_generated_tests(
            "复现并跑通测试", {"openresearch_run.py": code.encode()}, ResearchSpec()
        )


@pytest.mark.parametrize(
    "code", ["assert actual == expected", "np.testing.assert_allclose(a, b)", "unittest.main()"]
)
def test_generated_assertions_and_existing_test_runners_are_supported(code):
    validate_generated_tests(
        "run actual tests", {"openresearch_run.py": code.encode()}, ResearchSpec()
    )
