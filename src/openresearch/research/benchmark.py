import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from typing import Any

from openresearch.errors import ContractError
from openresearch.models.research import BenchmarkSpec


@dataclass(frozen=True)
class Dataset:
    inputs: list[dict[str, Any]]
    targets: dict[str, str | float]
    sha256: str


def finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ContractError("regression values must be finite numbers") from exc
    if isinstance(value, bool) or not math.isfinite(number):
        raise ContractError("regression values must be finite numbers")
    return number


def label(value: Any) -> str:
    if value is None or not isinstance(value, str | int | float | bool):
        raise ContractError("classification labels must be JSON scalar values")
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError("classification labels must be finite")
    return str(value)


def dataset_records(text: str, spec: BenchmarkSpec) -> Any:
    if spec.format == "json":
        records = json.loads(text)
        if not isinstance(records, list):
            raise ContractError("JSON benchmark dataset must be an array of records")
        return records
    if spec.format == "jsonl":
        return (json.loads(line) for line in text.splitlines() if line.strip())
    reader = csv.DictReader(io.StringIO(text), delimiter="\t" if spec.format == "tsv" else ",")
    names = reader.fieldnames or []
    if len(names) != len(set(names)) or any(not name for name in names):
        raise ContractError("dataset column names must be nonempty and unique")
    return reader


def parse_dataset(raw: bytes, spec: BenchmarkSpec, max_rows: int) -> Dataset:
    try:
        text = raw.decode("utf-8-sig")
        records = dataset_records(text, spec)
        inputs: list[dict[str, Any]] = []
        targets: dict[str, str | float] = {}
        for index, record in enumerate(records):
            if index >= max_rows or not isinstance(record, dict) or None in record:
                raise ContractError("dataset exceeds row limit or contains invalid records")
            required = [*spec.input_columns, spec.target_column]
            if spec.id_column:
                required.append(spec.id_column)
            if any(column not in record or record[column] is None for column in required):
                raise ContractError("dataset does not match the declared column mapping")
            identifier = str(record[spec.id_column]) if spec.id_column else str(index)
            if not identifier or identifier in targets or len(identifier.encode()) > 256:
                raise ContractError("dataset identifiers must be nonempty and unique")
            target = record[spec.target_column]
            targets[identifier] = (
                finite_number(target) if spec.task == "regression" else label(target)
            )
            inputs.append(
                {"id": identifier, "inputs": {key: record[key] for key in spec.input_columns}}
            )
    except (UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        raise ContractError("invalid UTF-8 benchmark dataset") from exc
    if not targets:
        raise ContractError("benchmark dataset is empty")
    return Dataset(inputs, targets, hashlib.sha256(raw).hexdigest())


def validate_predictions(
    dataset: Dataset, predictions: Any, spec: BenchmarkSpec
) -> dict[str, str | float]:
    if not isinstance(predictions, list) or len(predictions) != len(dataset.targets):
        raise ContractError("predictions must cover every dataset row exactly once")
    output: dict[str, str | float] = {}
    for row in predictions:
        if not isinstance(row, dict) or set(row) != {"id", "prediction"}:
            raise ContractError("prediction must contain only id and prediction")
        identifier = row["id"]
        if (
            not isinstance(identifier, str)
            or identifier not in dataset.targets
            or identifier in output
        ):
            raise ContractError("prediction identifiers are missing, duplicate, or unknown")
        value = row["prediction"]
        output[identifier] = finite_number(value) if spec.task == "regression" else label(value)
    return output


def classification_metrics(truth: list[str], predicted: list[str]) -> dict[str, float]:
    classes = set(truth) | set(predicted)
    f1 = []
    for category in sorted(classes):
        true_positive = sum(a == b == category for a, b in zip(truth, predicted, strict=True))
        denominator = sum(a == category for a in truth) + sum(b == category for b in predicted)
        f1.append(2 * true_positive / denominator if denominator else 0.0)
    return {
        "accuracy": sum(a == b for a, b in zip(truth, predicted, strict=True)) / len(truth),
        "macro_f1": math.fsum(f1) / len(f1),
    }


def regression_metrics(truth: list[float], predicted: list[float]) -> dict[str, float]:
    errors = [a - b for a, b in zip(truth, predicted, strict=True)]
    mean = math.fsum(truth) / len(truth)
    squared = math.fsum(error * error for error in errors)
    variance = math.fsum((value - mean) ** 2 for value in truth)
    r2 = 1 - squared / variance if variance else float(squared == 0)
    result = {
        "mse": squared / len(truth),
        "mae": math.fsum(abs(error) for error in errors) / len(truth),
        "r2": r2,
    }
    if any(not math.isfinite(value) for value in result.values()):
        raise ContractError("metric calculation overflowed; scale the dataset values")
    return result


def evaluate(dataset: Dataset, predictions: Any, spec: BenchmarkSpec) -> dict[str, float]:
    values = validate_predictions(dataset, predictions, spec)
    if spec.task == "classification":
        metrics = classification_metrics(
            [str(value) for value in dataset.targets.values()],
            [str(values[key]) for key in dataset.targets],
        )
    else:
        metrics = regression_metrics(
            [float(value) for value in dataset.targets.values()],
            [float(values[key]) for key in dataset.targets],
        )
    return {key: metrics[key] for key in spec.metrics}
