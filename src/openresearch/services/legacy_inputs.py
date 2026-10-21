"""旧任务输入只转为声明的数据与所属资源，不接收执行器权限或宿主路径。"""

import json
import re
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from openresearch.errors import ContractError
from openresearch.models.legacy import DirectExecutionRequest
from openresearch.sources.dependency_resolution import dependency_requests


def text_input(inputs: dict[str, Any], *names: str, limit: int = 131072) -> str | None:
    values = [inputs[name] for name in names if name in inputs and inputs[name] not in (None, "")]
    if not values:
        return None
    if any(not isinstance(value, str) or len(value) > limit for value in values):
        raise ContractError("legacy text input is invalid or exceeds its size limit")
    if len(set(values)) != 1:
        raise ContractError("conflicting aliases in legacy task inputs")
    return str(values[0])


def uploaded_inputs(payload: DirectExecutionRequest) -> list[str]:
    values = payload.inputs.get("attachments", [])
    if not isinstance(values, list) or len(values) > 8:
        raise ContractError("legacy attachments require at most eight upload IDs")
    identifiers = list(values)
    selected = text_input(payload.inputs, "upload_id", "dataset_upload_id", "paper_upload_id")
    if selected:
        identifiers.append(selected)
    path = text_input(payload.inputs, "dataset_path", "paper_path")
    if path:
        match = re.fullmatch(r"/api/uploads/([a-f0-9]{32})/content", path)
        if not match:
            raise ContractError("legacy file paths must refer to owned uploads, not host paths")
        identifiers.append(match[1])
    if any(
        not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value)
        for value in identifiers
    ):
        raise ContractError("legacy attachments must be valid upload IDs")
    return list(dict.fromkeys(identifiers))


def legacy_requirements(inputs: dict[str, Any]) -> list[str] | None:
    supplied = [
        inputs[key]
        for key in ("dependency_spec", "requirements", "dependencies")
        if key in inputs and inputs[key] not in (None, "")
    ]
    if not supplied:
        return None
    parsed = []
    for raw in supplied:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = [
                    part.strip().strip("`\"'")
                    for line in raw.splitlines()
                    for part in re.split(r",\s*(?=[A-Za-z])", line)
                    if part.strip()
                ]
        if isinstance(raw, dict):
            raw = raw.get("requirements", raw.get("dependencies", raw.get("packages")))
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise ContractError("legacy dependencies require package requirement strings")
        canonical = []
        for value in raw:
            try:
                item = Requirement(value)
            except InvalidRequirement:
                raise ContractError("invalid legacy dependency requirement") from None
            if item.url or item.extras or item.marker:
                raise ContractError("legacy dependencies require public wheel package requirements")
            normalized = canonicalize_name(item.name)
            name = {"pytorch": "torch", "msgpack-python": "msgpack"}.get(
                normalized, str(normalized)
            )
            canonical.append(name + str(item.specifier))
        parsed.append(dependency_requests(list(dict.fromkeys(canonical)), limit=128))
    if any(set(value) != set(parsed[0]) for value in parsed[1:]):
        raise ContractError("conflicting legacy dependency declarations")
    return list(parsed[0])
