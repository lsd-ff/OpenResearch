import asyncio
import re
from dataclasses import dataclass

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from openresearch.agents.code_repair import MODULE_PACKAGES
from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.sources import EnvironmentLock
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.dependency_resolution import DependencyResolver, dependency_requests
from openresearch.sources.stdlib import is_stdlib
from openresearch.sources.wheel_modules import wheel_sources
from openresearch.sources.wheels import WheelSource


@dataclass(frozen=True)
class RecoveredEnvironment:
    requests: list[str]
    lock: EnvironmentLock


def protected_requests(intent: str, current: list[str]) -> list[str]:
    names: list[str] = []
    for value in current:
        name = canonicalize_name(Requirement(value).name)
        if is_stdlib(name):
            continue
        aliases = {
            name,
            name.replace("-", "_"),
            *[
                root
                for root, package in MODULE_PACKAGES.items()
                if canonicalize_name(package) == name
            ],
        }
        if any(
            re.search(
                r"(?<![A-Za-z0-9_-])" + re.escape(alias) + r"(?![A-Za-z0-9_-])",
                re.sub(r"```.*?```", "", intent, flags=re.S),
                re.I,
            )
            for alias in aliases
        ):
            names.append(name)
    return names


def merge_requests(
    current: list[str],
    proposed: list[str],
    constraints: list[str],
    removals: list[str] | None = None,
) -> list[str]:
    changes = dependency_requests(proposed)
    merged: dict[str, str] = {
        canonicalize_name(Requirement(value).name): value
        for value in dependency_requests(current, limit=128)
    }
    removed = set(removals or [])
    # 只删除已声明的直接请求；用户约束和框架身份不能被模型的替换动作绕过。
    protected = {
        canonicalize_name(Requirement(value).name)
        for value in dependency_requests(constraints, limit=512)
    }
    if removed & protected:
        raise ContractError("dependency recovery cannot remove a user or framework requirement")
    if removed - set(merged):
        raise ContractError("dependency recovery can only remove existing direct requests")
    if removed & {canonicalize_name(Requirement(value).name) for value in changes}:
        raise ContractError("a dependency cannot be removed and added in the same proposal")
    for name in removed:
        merged.pop(name)
    for value in changes:
        if is_stdlib(Requirement(value).name):
            raise ContractError(
                "dependency recovery cannot replace Python standard library modules"
            )
        merged[canonicalize_name(Requirement(value).name)] = value
    for value in dependency_requests(constraints, limit=512):
        requirement = Requirement(value)
        name = canonicalize_name(requirement.name)
        previous = Requirement(merged.get(name, value))
        merged[name] = str(name) + str(previous.specifier & requirement.specifier)
    return dependency_requests(list(merged.values()), limit=128)


class DependencyRecovery:
    def __init__(self, settings: Settings) -> None:
        self.resolver = DependencyResolver()
        self.source = WheelSource(settings)
        self.blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)

    async def resolve(
        self,
        current: list[str],
        proposed: list[str],
        missing: str | None = None,
        constraints: list[str] | None = None,
        *,
        removals: list[str] | None = None,
    ) -> RecoveredEnvironment:
        if not proposed and not removals:
            raise ContractError("dependency repair must name an actual package")
        requests = merge_requests(current, proposed, constraints or [], removals)
        if requests == current:
            raise ContractError("dependency repair did not change the requested environment")
        spec = await (
            self.resolver.resolve(requests)
            if len(requests) <= 32
            else self.resolver.resolve_recovery(requests)
        )
        lock, wheels = await self.source.fetch(spec)
        if missing:
            # 只能采用确实包含缺失模块的已校验 wheel，不在宿主机导入依赖。
            await asyncio.to_thread(wheel_sources, wheels, [missing])
        for raw in wheels.values():
            await asyncio.to_thread(self.blobs.write, raw)
        return RecoveredEnvironment(requests, lock)
