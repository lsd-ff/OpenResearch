import pytest
from pydantic import ValidationError

from openresearch.models.planning import IntentDraft


def test_absent_columns_do_not_require_model_invention():
    assert (
        IntentDraft.model_validate({"workflow": "reproduce", "input_columns": None}).input_columns
        == []
    )
    assert IntentDraft(workflow="benchmark", input_columns=["text"]).input_columns == ["text"]


@pytest.mark.parametrize("columns", ["text", {"text": True}, [None], ["text"] * 17])
def test_invalid_explicit_columns_are_still_rejected(columns):
    with pytest.raises(ValidationError):
        IntentDraft.model_validate({"workflow": "benchmark", "input_columns": columns})
