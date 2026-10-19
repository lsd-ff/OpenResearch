import csv
import io
import json
import math
import statistics
from html import escape
from typing import Any

from openresearch.errors import ContractError


def read_records(raw: bytes, name: str, max_rows: int) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8-sig")
        suffix = name.rsplit(".", 1)[-1].lower()
        if suffix == "json":
            records = json.loads(text)
        elif suffix == "jsonl":
            records = [json.loads(line) for line in text.splitlines() if line.strip()]
        elif suffix in {"csv", "tsv"}:
            reader = csv.DictReader(io.StringIO(text), delimiter="\t" if suffix == "tsv" else ",")
            names = reader.fieldnames or []
            if not names or len(names) != len(set(names)) or any(not name for name in names):
                raise ContractError("dataset column names must be nonempty and unique")
            records = []
            for row in reader:
                if len(records) >= max_rows:
                    raise ContractError("dataset exceeds configured row limit")
                records.append(row)
        else:
            raise ContractError("data analysis supports CSV, TSV, JSON and JSONL")
    except (UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        raise ContractError("invalid UTF-8 dataset") from exc
    if not isinstance(records, list) or not records or len(records) > max_rows:
        raise ContractError("dataset must contain records within the configured row limit")
    columns = set(records[0]) if isinstance(records[0], dict) else set()
    if not columns or len(columns) > 128 or any(not isinstance(key, str) for key in columns):
        raise ContractError("dataset needs between 1 and 128 named columns")
    if any(not isinstance(row, dict) or set(row) != columns or None in row for row in records):
        raise ContractError("dataset rows must have consistent named columns")
    return records


def summarize_column(values: list[Any]) -> dict[str, Any]:
    present = [value for value in values if value is not None and value != ""]
    result: dict[str, Any] = {"count": len(values), "missing": len(values) - len(present)}
    numbers: list[float] = []
    for value in present:
        if isinstance(value, bool):
            return result
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return result
        if not math.isfinite(number):
            raise ContractError("numeric dataset values must be finite")
        numbers.append(number)
    if numbers:
        result.update(
            min=min(numbers),
            max=max(numbers),
            mean=statistics.mean(numbers),
            median=statistics.median(numbers),
        )
    return result


def profile_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    columns = {key: summarize_column([row[key] for row in records]) for key in records[0]}
    return {"version": "data.profile/v1", "row_count": len(records), "columns": columns}


def missing_values_chart(profile: dict[str, Any]) -> str:
    columns = list(profile["columns"].items())[:32]
    bars = []
    for index, (name, stats) in enumerate(columns):
        y = 40 + index * 28
        width = 360 * stats["missing"] / stats["count"]
        bars.append(
            f'<text x="8" y="{y + 14}">{escape(name[:24])}</text>'
            f'<rect x="200" y="{y}" width="{width:.2f}" height="18" fill="#2563eb"/>'
            f'<text x="570" y="{y + 14}">{stats["missing"]}/{stats["count"]}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="680" height="{60 + len(bars) * 28}" '
        'role="img" aria-label="Missing values per column">'
        '<rect width="100%" height="100%" fill="white"/>'
        '<g font-family="sans-serif" font-size="13" fill="#172554">'
        '<text x="8" y="22">Missing values per column (missing / total)</text>'
        + "".join(bars)
        + "</g></svg>"
    )
