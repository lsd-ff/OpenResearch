"""只在管理员注册的 CPU 运行时之间选择，并固定配置摘要。"""

import re

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.runtime import Task
from openresearch.models.runtime_profiles import RuntimeBinding, RuntimeProfile, TargetPython
from openresearch.sandbox.profiles import profile_fingerprint, selected_profile


def intent_python(intent: str) -> SpecifierSet | None:
    plain = re.sub(r"```.*?```", "", intent, flags=re.S)
    version = r"3\.\d+(?:\.\d+|\.\*)?"
    parts = re.findall(
        r"(?<![A-Za-z0-9_])python\s*("
        r"(?:[<>=!~]{1,2}\s*)?" + version + r"(?:\s*,\s*[<>=!~]{1,2}\s*" + version + r")*)",
        plain,
        re.I,
    )
    if not parts:
        return None
    constraints = []
    for part in parts:
        normalized = re.sub(r"\s+", "", part)
        if normalized.startswith("3."):
            normalized = "==" + normalized + (".*" if normalized.count(".") == 1 else "")
        elif normalized.startswith("=") and not normalized.startswith("=="):
            normalized = "=" + normalized
        constraints.append(normalized)
    return SpecifierSet(",".join(constraints))


def bind_runtime(settings: Settings, name: str) -> RuntimeBinding:
    profile = selected_profile(settings, name)
    if profile is None:
        raise ContractError("runtime profile must be registered")
    return RuntimeBinding(profile=name, sha256=profile_fingerprint(profile))


def bound_profile(settings: Settings, binding: RuntimeBinding | None) -> RuntimeProfile | None:
    if binding is None:
        return None
    profile = selected_profile(settings, binding.profile)
    if profile is None or profile_fingerprint(profile) != binding.sha256:
        raise ContractError("registered runtime configuration changed after the plan was frozen")
    return profile


def runtime_python(settings: Settings, binding: RuntimeBinding | None) -> TargetPython:
    profile = bound_profile(settings, binding)
    return profile.python if profile else "3.12"


def possible_python(python: TargetPython, requested: SpecifierSet) -> bool:
    # 模板只知道次版本；边界附近的整数补丁判断是否可能匹配，实际值必须随后在容器核验。
    patches = {0, 1}
    for specifier in requested:
        match = re.fullmatch(re.escape(python) + r"\.([0-9]+)", specifier.version)
        if match:
            patch = int(match[1])
            patches.update((max(0, patch - 1), patch, patch + 1))
    return any(f"{python}.{patch}" in requested for patch in patches)


def runtime_for_intent(
    settings: Settings,
    intent: str,
    binding: RuntimeBinding | None = None,
    *,
    allow_gpu: bool = False,
) -> RuntimeBinding | None:
    requested = intent_python(intent)
    profile = bound_profile(settings, binding)
    if profile:
        if profile.gpu and not allow_gpu:
            raise ContractError("research GPU profiles require GPU resource admission")
        if requested is not None and not possible_python(profile.python, requested):
            raise ContractError("configured runtime conflicts with the requested Python version")
        return binding
    if requested is not None:
        if possible_python("3.12", requested):
            return None
        for name, candidate in sorted(settings.runtime_profiles.items()):
            if possible_python(candidate.python, requested) and not candidate.gpu:
                return bind_runtime(settings, name)
        raise ContractError("no registered CPU runtime satisfies the requested Python version")
    return None


def runtime_tools(settings: Settings) -> list[str]:
    return (
        ["runtime.select"]
        if any(not item.gpu for item in settings.runtime_profiles.values())
        else []
    )


def task_runtime(settings: Settings, task: Task, intent: str) -> RuntimeBinding | None:
    raw = task.inputs.get("runtime")
    binding = RuntimeBinding.model_validate(raw) if raw is not None else None
    if "runtime.select" not in task.contract.allowed_tools:
        if binding:
            raise ContractError("task is not authorized to select a runtime")
        return None
    return runtime_for_intent(
        settings, intent, binding, allow_gpu="runtime.probe" in task.contract.allowed_tools
    )


def compatible_runtime(
    settings: Settings,
    intent: str,
    current: RuntimeBinding | None,
    evidence: list[dict[str, str]],
    requests: list[str],
    *,
    versions: dict[str, str] | None = None,
) -> RuntimeBinding | None:
    requested = intent_python(intent)
    if runtime_change_forbidden(intent):
        return None
    bounds = package_python_bounds(evidence, requests)
    python = runtime_python(settings, current)
    actual = versions.get(current.profile if current else "") if versions else python + ".0"
    if not bounds or actual is None or all(actual in bound for bound in bounds):
        return None
    for name, candidate in sorted(
        settings.runtime_profiles.items(), key=lambda item: item[1].python
    ):
        version = versions.get(name) if versions is not None else candidate.python + ".0"
        different = (
            name != (current.profile if current else "") if versions else candidate.python != python
        )
        if (
            not candidate.gpu
            and different
            and version is not None
            and (requested is None or version in requested)
            and all(version in bound for bound in bounds)
        ):
            return bind_runtime(settings, name)
    return None


def runtime_change_forbidden(intent: str) -> bool:
    return bool(
        re.search(
            r"(?:不(?:要|允许|得)?|禁止|请勿|不能).{0,6}(?:切换|调整|修改|更换|升级|降级).{0,8}(?:Python|运行时)|"
            r"(?:固定|保持|保留).{0,6}(?:Python|运行时).{0,4}版本|"
            r"(?:do not|never|without)\s+(?:switch|change|upgrade|downgrade).{0,12}"
            r"(?:python|runtime)",
            re.sub(r"```.*?```", "", intent, flags=re.S),
            re.I,
        )
    )


def package_python_bounds(
    evidence: list[dict[str, str]], requests: list[str]
) -> list[SpecifierSet]:
    return [
        SpecifierSet(item["requires_python"])
        for item in evidence
        if item["requires_python"]
        and any(
            canonicalize_name(Requirement(value).name) == canonicalize_name(item["name"])
            and item["version"] in Requirement(value).specifier
            for value in requests
        )
    ]
