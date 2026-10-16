"""读取规划所需的有限元数据；上传源码与论文解析仍只进入沙箱。"""

import csv
import io
import json
import re
from dataclasses import dataclass
from itertools import islice
from pathlib import PurePosixPath
from typing import Any

from openresearch.errors import ContractError
from openresearch.models.documents import UploadRecord
from openresearch.models.research import BenchmarkSpec
from openresearch.models.sources import RepositorySpec
from openresearch.services.uploads import UploadService

DATA_EXTENSIONS = {".csv": "csv", ".tsv": "tsv", ".jsonl": "jsonl", ".json": "json"}


@dataclass(frozen=True)
class DatasetProfile:
    record: UploadRecord
    format: str
    columns: list[str]
    rows: int

    def model_context(self) -> dict[str, Any]:
        return {
            "upload_id": self.record.id,
            "name": self.record.name,
            "columns": self.columns,
            "row_count": self.rows,
        }


def profile_dataset(record: UploadRecord, raw: bytes, max_rows: int) -> DatasetProfile:
    format_name = DATA_EXTENSIONS[PurePosixPath(record.name).suffix.lower()]
    try:
        text = raw.decode("utf-8-sig")
        if format_name == "json":
            records = json.loads(text)
            if not isinstance(records, list):
                raise ContractError("JSON 数据集需要是对象数组。")
        elif format_name == "jsonl":
            records = [
                json.loads(line)
                for line in islice(
                    (line for line in text.splitlines() if line.strip()), max_rows + 1
                )
            ]
        else:
            reader = csv.DictReader(
                io.StringIO(text), delimiter="\t" if format_name == "tsv" else ","
            )
            fields = reader.fieldnames or []
            if len(fields) != len(set(fields)) or any(not field for field in fields):
                raise ContractError("数据列名必须非空且不重复。")
            records = list(islice(reader, max_rows + 1))
    except (UnicodeError, csv.Error, json.JSONDecodeError):
        raise ContractError("无法读取 UTF-8 表格数据，请检查文件格式。") from None
    if not records or len(records) > max_rows:
        raise ContractError(f"数据集需要包含 1-{max_rows} 行；请在聊天中上传符合预算的数据。")
    columns = list(records[0]) if isinstance(records[0], dict) else []
    if (
        not columns
        or len(columns) > 64
        or any(not isinstance(key, str) or not key for key in columns)
    ):
        raise ContractError("数据集需要包含有效且有界的命名列。")
    if any(not isinstance(row, dict) or set(row) != set(columns) for row in records):
        raise ContractError("数据集中每行的列必须一致。")
    return DatasetProfile(record, format_name, columns, len(records))


def dataset_profiles(
    uploads: UploadService, identifiers: list[str], owner_id: str, max_rows: int
) -> list[DatasetProfile]:
    result = []
    for identifier in identifiers:
        record = uploads.repository.uploads.get(identifier, owner_id)
        if PurePosixPath(record.name).suffix.lower() in DATA_EXTENSIONS:
            _, raw = uploads.content(identifier, owner_id)
            result.append(profile_dataset(record, raw, max_rows))
    return result


def repository_urls(text: str) -> list[str]:
    urls: list[str] = []
    for value in re.findall(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        try:
            url = RepositorySpec(url=value.rstrip(".,;:")).url
        except ValueError:
            continue
        if url not in urls:
            urls.append(url)
    return urls


def explicit_column(goal: str, column: str, role: str) -> bool:
    """提到某列的取值不等于声明它是标签；角色必须在用户原话中相邻指定。"""
    name = re.escape(column)
    forward = (
        rf"(?:{role})(?:_column|\s*column|列)?[\s`\"':：=]*"
        rf"(?:(?:是|为|用|取|is|named|叫)\s*)?[`\"']?{name}(?![\w])"
    )
    backward = rf"(?<![\w]){name}[`\"']?\s*(?:作为|用作|是|为|is|as)\s*(?:{role})(?:列)?"
    return bool(re.search(forward, goal, re.I) or re.search(backward, goal, re.I))


def benchmark_mapping(
    profile: DatasetProfile, proposal: dict[str, Any], goal: str
) -> tuple[BenchmarkSpec | None, str]:
    columns = profile.columns
    target = proposal.get("target_column")
    if target and not explicit_column(goal, target, "目标|标签|target|label|ground_truth"):
        target = None
    if not target:
        conventional = [key for key in columns if key.lower() in {"y", "label", "target", "class"}]
        target = conventional[0] if len(conventional) == 1 else None
    if target not in columns:
        return None, f"数据列为 {', '.join(columns)}。请指出目标/标签列，其余列可自动用作输入。"
    identifier = proposal.get("id_column")
    if identifier and not explicit_column(goal, identifier, "ID|标识|identifier|sample_id"):
        identifier = None
    identifier = identifier or next(
        (key for key in columns if key.lower() == "id" and key != target), None
    )
    inputs = proposal.get("input_columns")
    if not inputs or not any(
        explicit_column(goal, key, "输入|特征|input|feature") for key in inputs
    ):
        inputs = [key for key in columns if key not in {target, identifier}]
    if not inputs or any(
        key not in columns for key in [*inputs, target, *([identifier] if identifier else [])]
    ):
        return None, f"请根据实际数据列 {', '.join(columns)} 补充输入列和目标列。"
    try:
        return (
            BenchmarkSpec(
                upload_id=profile.record.id,
                format=profile.format,
                input_columns=inputs,
                target_column=target,
                id_column=identifier,
                task=proposal.get("task", "classification"),
                metrics=["mse", "mae", "r2"]
                if proposal.get("task") == "regression"
                else ["accuracy", "macro_f1"],
            ),
            "",
        )
    except ValueError:
        return None, "输入列应与目标列、ID 列分开；请在聊天中补充正确的列映射。"
