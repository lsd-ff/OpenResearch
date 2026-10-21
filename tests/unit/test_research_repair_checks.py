import pytest

from openresearch.errors import ContractError
from openresearch.research.code_guards import validate_code_repair


def test_operational_runtime_error_can_be_fixed_without_rewriting_existing_checks():
    before = "value = 3\nassert value == 3\nraise RuntimeError('implementation fault')\n"
    after = "value = 3\nassert value == 3\nprint(value)\n"
    validate_code_repair(before, after, preserve_raises=False)
    with pytest.raises(ContractError):
        validate_code_repair(before, after)


def test_runtime_implementation_can_add_real_checks_but_cannot_exit_before_old_checks():
    validate_code_repair(
        "raise RuntimeError('incomplete')\n",
        "assert sum([2,3]) == 5\nprint(5)\n",
        preserve_raises=False,
    )
    for candidate in [
        "raise SystemExit(0)\nassert sum([2,3]) == 5\n",
        "import pytest\npytest.skip('bypass')\nassert sum([2,3]) == 5\n",
    ]:
        with pytest.raises(ContractError):
            validate_code_repair("assert sum([2,3]) == 5\n", candidate, preserve_raises=False)


@pytest.mark.parametrize(
    "before,after",
    [
        ("assert sum([2,3]) == 5\nprint(5)\n", "print(5)\n"),
        ("raise AssertionError('must fail')\n", "print(5)\n"),
        ("raise SystemExit(1)\n", "print(5)\n"),
        (
            "from builtins import AssertionError as Check\nraise Check('must fail')\n",
            "from builtins import AssertionError as Check\nprint(Check)\n",
        ),
        (
            "def test_validation():\n    raise ValueError('expected failure')\n",
            "def test_validation():\n    return 5\n",
        ),
        (
            "try:\n    assert False\nexcept AssertionError:\n    raise\n",
            "try:\n    assert False\nexcept AssertionError:\n    pass\n",
        ),
        (
            "import numpy as np\nnp.testing.assert_allclose([1], [2])\nprint(np.arange(3))\n",
            "import numpy as np\nprint(np.arange(3))\n",
        ),
        (
            "from numpy.testing import assert_allclose\n"
            "assert_allclose([1], [2])\nprint(assert_allclose)\n",
            "from numpy.testing import assert_allclose\nprint(assert_allclose)\n",
        ),
    ],
)
def test_runtime_repair_still_cannot_remove_tests_assertions_or_failure_signals(before, after):
    with pytest.raises(ContractError):
        validate_code_repair(before, after, preserve_raises=False)
