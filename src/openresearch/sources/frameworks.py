import asyncio
import io
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.comparison import FrameworkSource, FrameworkTarget
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec, PackageDescription
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.dependency_resolution import DependencyResolver
from openresearch.sources.packages import PackageSource
from openresearch.sources.wheels import WheelSource

KNOWN = {
    "langchain": (["langchain-core"], ["langchain_core"]),
    "langchaincore": (["langchain-core"], ["langchain_core"]),
    "llamaindex": (["llama-index-core"], ["llama_index.core"]),
    "llamaindexcore": (["llama-index-core"], ["llama_index.core"]),
}


def wheel_sources(wheels: dict[str, bytes], roots: list[str]) -> dict[str, str]:
    excerpts: dict[str, str] = {}
    found: set[str] = set()
    for raw in wheels.values():
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for root in roots:
                prefix = root.replace(".", "/")
                members = [
                    item
                    for item in archive.infolist()
                    if item.filename.startswith(prefix + "/")
                    or item.filename == prefix + ".py"
                    or (item.filename.startswith(prefix + ".") and item.filename.endswith(".so"))
                ]
                if members:
                    found.add(root)
                candidates = sorted(
                    [item for item in members if item.filename.endswith(".py")],
                    key=lambda item: (
                        not any(
                            word in item.filename for word in ("in_memory", "base.py", "retriever")
                        ),
                        len(item.filename),
                    ),
                )
                for item in candidates[:3]:
                    if item.file_size > 512000 or len(excerpts) >= 8:
                        continue
                    excerpts[item.filename] = archive.read(item)[:14000].decode(
                        "utf-8", errors="replace"
                    )
    if found != set(roots):
        raise ContractError("requested framework modules do not exist in verified wheels")
    return excerpts


class FrameworkSources:
    def __init__(self, settings: Settings) -> None:
        self.packages = PackageSource(settings)
        self.wheels = WheelSource(settings)
        self.blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)
        self.resolver = DependencyResolver()
        self.llm = LLMClient(settings)

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
        self, target: FrameworkTarget
    ) -> tuple[EnvironmentSpec, EnvironmentLock, FrameworkSource]:
        target = await self.target(target)
        requests = list(target.requirements)
        # InMemoryVectorStore 的余弦检索将 NumPy 作为可选依赖；实际适配器必须显式锁定。
        if "langchain_core" in target.import_roots and not any(
            canonicalize_name(Requirement(item).name) == "numpy" for item in requests
        ):
            requests.append("numpy>=1.26,<3")
        environment = await self.resolver.resolve(requests)
        packages = await self.describe(target, environment)
        lock, wheels = await self.wheels.fetch(environment)
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
