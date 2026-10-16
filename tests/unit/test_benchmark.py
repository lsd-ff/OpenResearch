import pytest

from openresearch.errors import ContractError
from openresearch.models.research import BenchmarkSpec
from openresearch.research.benchmark import evaluate, parse_dataset


def spec(task="classification", metrics=None) -> BenchmarkSpec:
    return BenchmarkSpec(
        upload_id="source",
        input_columns=["x"],
        target_column="y",
        id_column="id",
        task=task,
        metrics=metrics or ["accuracy"],
    )


def test_dataset_excludes_target_and_preserves_identifiers() -> None:
    dataset = parse_dataset(b"id,x,y\na,1,yes\nb,2,no\n", spec(), 100)
    assert dataset.inputs == [{"id": "a", "inputs": {"x": "1"}}, {"id": "b", "inputs": {"x": "2"}}]
    assert dataset.targets == {"a": "yes", "b": "no"}
    scores = evaluate(
        dataset, [{"id": "b", "prediction": "no"}, {"id": "a", "prediction": "yes"}], spec()
    )
    assert scores == {"accuracy": 1.0}


@pytest.mark.parametrize(
    "predictions",
    [
        [],
        [{"id": "a", "prediction": "yes"}],
        [{"id": "a", "prediction": "yes"}, {"id": "a", "prediction": "yes"}],
        [{"id": "a", "prediction": "yes"}, {"id": "unknown", "prediction": "no"}],
    ],
)
def test_incomplete_duplicate_or_unknown_predictions_are_rejected(predictions) -> None:
    dataset = parse_dataset(b"id,x,y\na,1,yes\nb,2,no\n", spec(), 100)
    with pytest.raises(ContractError):
        evaluate(dataset, predictions, spec())


def test_regression_scores_are_recomputed_and_nonfinite_values_rejected() -> None:
    mapping = spec("regression", ["mse", "mae", "r2"])
    dataset = parse_dataset(b"id,x,y\na,1,2\nb,2,4\n", mapping, 100)
    scores = evaluate(
        dataset, [{"id": "a", "prediction": 2}, {"id": "b", "prediction": 3}], mapping
    )
    assert scores == {"mse": 0.5, "mae": 0.5, "r2": 0.5}
    with pytest.raises(ContractError, match="finite"):
        evaluate(
            dataset,
            [{"id": "a", "prediction": float("nan")}, {"id": "b", "prediction": 3}],
            mapping,
        )


def test_dataset_budget_and_duplicate_identifiers_are_rejected() -> None:
    with pytest.raises(ContractError):
        parse_dataset(b"id,x,y\na,1,yes\na,2,no\n", spec(), 100)
    with pytest.raises(ContractError):
        parse_dataset(b"id,x,y\na,1,yes\nb,2,no\n", spec(), 1)


@pytest.mark.parametrize("raw", [b"id,x,x,y\na,1,2,yes\n", b"id,x,y\na,1,yes,extra\n"])
def test_ambiguous_dataset_columns_are_rejected(raw) -> None:
    with pytest.raises(ContractError):
        parse_dataset(raw, spec(), 100)
