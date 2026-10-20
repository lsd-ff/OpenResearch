import pytest

from openresearch.errors import ContractError
from openresearch.research.code_guards import validate_code_repair


def test_api_repair_keeps_independent_assertions_and_real_tests():
    original = "import json\nvalue = json.load('{}')\nassert value == {}\n"
    validate_code_repair(original, original.replace("json.load(", "json.loads("))


@pytest.mark.parametrize(
    "candidate",
    [
        "import json\nvalue = {}\nprint('passed')\n",
        "import json\nvalue = {}\nif False:\n    assert value == {}\n",
        "import json\nvalue = {}\nassert True\n",
        "import json, sys\nvalue = {}\nsys.exit(0)\nassert value == {}\n",
        "import json\nvalue = {}\ntry:\n    assert value == {}\nexcept Exception:\n    pass\n",
    ],
)
def test_repair_cannot_drop_weaken_hide_or_swallow_assertions(candidate):
    with pytest.raises(ContractError):
        validate_code_repair(
            "import json\nvalue = json.load('{}')\nassert value == {}\n", candidate
        )


def test_moving_a_check_between_conditional_branches_is_rejected():
    source = "if enabled:\n    assert result == 1\nelse:\n    pass\n"
    with pytest.raises(ContractError):
        validate_code_repair(source, "if enabled:\n    pass\nelse:\n    assert result == 1\n")


def test_test_function_and_invocation_are_both_preserved():
    source = "def test_api():\n    assert 1 + 1 == 2\ntest_api()\n"
    with pytest.raises(ContractError):
        validate_code_repair(source, source.replace("test_api()\n", "pass\n"))


def test_syntax_repair_cannot_remove_existing_check_lines():
    source = "if True\n    assert 1 + 1 == 2\n"
    validate_code_repair(source, source.replace("True\n", "True:\n"))
    with pytest.raises(ContractError):
        validate_code_repair(source, "if True:\n    pass\n")


@pytest.mark.parametrize(
    "candidate",
    [
        "if False:\n    assert 1 + 1 == 2\n",
        "if True:\n    import sys\n    sys.exit(0)\n    assert 1 + 1 == 2\n",
    ],
)
def test_syntax_repair_cannot_hide_existing_checks_by_changing_the_guard(candidate):
    with pytest.raises(ContractError):
        validate_code_repair("if True\n    assert 1 + 1 == 2\n", candidate)


def test_repair_cannot_replace_a_real_library_with_a_literal_or_unreachable_import():
    source = "import json\nvalue = json.decode('{}')\nassert value == {}\n"
    with pytest.raises(ContractError):
        validate_code_repair(source, "import json\nvalue = {}\nassert value == {}\n")
    with pytest.raises(ContractError):
        validate_code_repair(
            source, "if False:\n    import json\nvalue = json.loads('{}')\nassert value == {}\n"
        )


def test_a_missing_import_can_be_added_and_an_unused_import_can_be_removed():
    validate_code_repair("print(os.getcwd())", "import os\nprint(os.getcwd())")
    validate_code_repair("import unused_package\nprint(7)", "print(7)")
