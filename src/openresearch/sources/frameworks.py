import asyncio
from dataclasses import dataclass

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.comparison import FrameworkSource, FrameworkTarget
from openresearch.models.environment_preparation import EnvironmentPreparationReport
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec, PackageDescription
from openresearch.research.environment_preparation import EnvironmentPreparation
from openresearch.research.interpreter import InterpreterProbe
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.dependency_resolution import DependencyResolver
from openresearch.sources.packages import PackageSource
from openresearch.sources.wheel_modules import wheel_sources
from openresearch.sources.wheels import WheelSource

KNOWN = {
    "langchain": (["langchain-core"], ["langchain_core"]),
    "langchaincore": (["langchain-core"], ["langchain_core"]),
    "llamaindex": (["llama-index-core"], ["llama_index.core"]),
    "llamaindexcore": (["llama-index-core"], ["llama_index.core"]),
}


def framework_requests(target: FrameworkTarget) -> list[str]:
    requests = list(target.requirements)
    # 实际 LangChain 适配器会调用向量相似度计算，必须锁定其可选 NumPy 依赖。
    if "langchain_core" in target.import_roots and not any(
        canonicalize_name(Requirement(value).name) == "numpy" for value in requests
    ):
        requests.append("numpy>=1.26,<3")
    return requests


@dataclass(frozen=True)
class PreparedFramework:
    report: EnvironmentPreparationReport
    environment: EnvironmentSpec | None = None
    lock: EnvironmentLock | None = None
    source: FrameworkSource | None = None


class FrameworkSources:
    def __init__(self, settings: Settings) -> None:
        self.packages = PackageSource(settings)
        self.wheels = WheelSource(settings)
        self.blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)
        self.resolver = DependencyResolver(source_proxy_url=settings.source_proxy_url)
        self.llm = LLMClient(settings)
        self.preparation = EnvironmentPreparation(settings)

    async def target(self, target: FrameworkTarget) -> FrameworkTarget:
        known = KNOWN.get(target.name.casefold().replace("-", "").replace(" ", ""))
        if target.requirements:
            if known and not target.import_roots:
                return target.model_copy(update={"import_roots": known[1]})
            return target
        if known:
            return FrameworkTarget(name=target.name, requirements=known[0], import_roots=known[1])
        proposal = await self.llm.request_typed(
            [
                {
                    "role": "system",
                    "content": "Identify public PyPI package requirements and actual "
                    "Python import_roots for the named framework. Return FrameworkTarget. "
                    "Do not invent a package or substitute a different framework.",
                },
                {"role": "user", "content": target.name},
            ],
            FrameworkTarget,
        )
        if not proposal.requirements or not proposal.import_roots:
            raise ContractError("framework package and actual import roots are required")
        return proposal.model_copy(update={"name": target.name})

    async def describe(
        self, target: FrameworkTarget, environment: EnvironmentSpec | None = None
    ) -> list[PackageDescription]:
        pins = dict(pin.split("==", 1) for pin in environment.requirements) if environment else {}
        packages = []
        for value in target.requirements:
            requirement = Requirement(value)
            name = canonicalize_name(requirement.name)
            version = pins.get(name)
            exact = list(requirement.specifier)
            if version is None and len(exact) == 1 and exact[0].operator == "==":
                version = exact[0].version
            package = await self.packages.describe(name, version)
            if package.version not in requirement.specifier:
                resolved = await self.resolver.resolve([value])
                version = dict(pin.split("==", 1) for pin in resolved.requirements)[name]
                package = await self.packages.describe(name, version)
            packages.append(package)
        return packages

    async def lock(
        self, target: FrameworkTarget, *, probe: InterpreterProbe | None = None
    ) -> tuple[EnvironmentSpec, EnvironmentLock, FrameworkSource]:
        target = await self.target(target)
        requests = framework_requests(target)
        resolver, source = self.resolver, self.wheels
        if probe:
            identity = await probe(None)
            resolver, source = resolver.for_interpreter(identity), source.for_interpreter(identity)
        environment = await resolver.resolve(requests)
        packages = await self.describe(target, environment)
        lock, wheels = await source.fetch(environment)
        for raw in wheels.values():
            await asyncio.to_thread(self.blobs.write, raw)
        roots = target.import_roots
        if not roots:
            roots = [package.name.replace("-", "_") for package in packages]
        excerpts = await asyncio.to_thread(wheel_sources, wheels, roots)
        return (
            environment,
            lock,
            FrameworkSource(packages=packages, import_roots=roots, api_excerpts=excerpts),
        )

    async def prepare(
        self,
        target: FrameworkTarget,
        intent: str,
        max_repairs: int,
        *,
        runtime: RuntimeBinding | None = None,
        allow_runtime_select: bool = False,
        probe: InterpreterProbe | None = None,
    ) -> PreparedFramework:
        constraints = list(target.requirements)
        target = await self.target(target)
        requests = framework_requests(target)
        constraints.extend(
            canonicalize_name(Requirement(value).name) for value in target.requirements
        )
        self.preparation.resolver, self.preparation.source = self.resolver, self.wheels
        self.preparation.packages = self.packages
        prepared = await self.preparation.run(
            intent,
            requests,
            constraints,
            max_repairs,
            lock_wheels=True,
            runtime=runtime,
            allow_runtime_select=allow_runtime_select,
            probe=probe,
        )
        if not prepared.report.success or prepared.spec is None or prepared.lock is None:
            return PreparedFramework(prepared.report)
        pins = dict(value.split("==", 1) for value in prepared.spec.requirements)
        primary = [canonicalize_name(Requirement(value).name) for value in target.requirements]
        # 修复后的 API 证据必须取自实际采用版本，不能继续引用原失败提议的版本。
        actual = target.model_copy(
            update={"requirements": [str(name) + "==" + pins[name] for name in primary]}
        )
        packages = await self.describe(actual, prepared.spec)
        roots = target.import_roots or [package.name.replace("-", "_") for package in packages]
        wheels = {
            wheel.filename: await asyncio.to_thread(self.blobs.read, wheel.sha256, wheel.size)
            for wheel in prepared.lock.wheels
        }
        excerpts = await asyncio.to_thread(wheel_sources, wheels, roots)
        source = FrameworkSource(packages=packages, import_roots=roots, api_excerpts=excerpts)
        return PreparedFramework(prepared.report, prepared.spec, prepared.lock, source)
