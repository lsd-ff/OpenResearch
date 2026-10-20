import asyncio
import sys
from dataclasses import dataclass

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.sources import EnvironmentLock
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.dependency_resolution import DependencyResolver, dependency_requests
from openresearch.sources.frameworks import wheel_sources
from openresearch.sources.wheels import WheelSource


@dataclass(frozen=True)
class RecoveredEnvironment:
    requests: list[str]
    lock: EnvironmentLock


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
    ) -> RecoveredEnvironment:
        if not proposed:
            raise ContractError("dependency repair must name an actual package")
        merged = {canonicalize_name(Requirement(value).name): value for value in current}
        for value in dependency_requests(proposed):
            if Requirement(value).name.casefold().replace("-", "_") in sys.stdlib_module_names:
                raise ContractError(
                    "dependency recovery cannot replace Python standard library modules"
                )
            merged[canonicalize_name(Requirement(value).name)] = value
        for value in dependency_requests(constraints or []):
            requirement = Requirement(value)
            name = canonicalize_name(requirement.name)
            previous = Requirement(merged.get(name, value))
            merged[name] = str(name) + str(previous.specifier & requirement.specifier)
        requests = dependency_requests(list(merged.values()))
        if requests == current:
            raise ContractError("dependency repair did not change the requested environment")
        spec = await self.resolver.resolve(requests)
        lock, wheels = await self.source.fetch(spec)
        if missing:
            # 只能采用确实包含缺失模块的已校验 wheel，不在宿主机导入依赖。
            await asyncio.to_thread(wheel_sources, wheels, [missing])
        for raw in wheels.values():
            await asyncio.to_thread(self.blobs.write, raw)
        return RecoveredEnvironment(requests, lock)
