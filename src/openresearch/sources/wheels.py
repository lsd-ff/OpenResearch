import asyncio
import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

import httpx
from packaging.markers import default_environment
from packaging.specifiers import SpecifierSet
from packaging.tags import Tag, compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from openresearch.config import Settings
from openresearch.errors import ContractError, EnvironmentLockError
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec, LockedWheel
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.dependency_closure import validate_dependency_graph
from openresearch.sources.http import read_bounded


def target_tags() -> set[Tag]:
    platforms = [f"manylinux_2_{minor}_x86_64" for minor in range(36, 16, -1)]
    platforms += [
        "manylinux2014_x86_64",
        "manylinux2010_x86_64",
        "manylinux1_x86_64",
        "linux_x86_64",
    ]
    return set(cpython_tags((3, 12), ["cp312"], platforms)) | set(
        compatible_tags((3, 12), "cp312", platforms)
    )


def validate_closure(metadata: dict[str, dict[str, Any]], pins: dict[str, str]) -> None:
    environment = {key: str(value) for key, value in default_environment().items()}
    environment.update(
        python_version="3.12",
        python_full_version="3.12.0",
        sys_platform="linux",
        platform_system="Linux",
        platform_machine="x86_64",
        os_name="posix",
        extra="",
    )
    try:
        validate_dependency_graph(
            {name: item["info"].get("requires_dist") or [] for name, item in metadata.items()},
            pins,
            environment,
        )
    except ValueError as exc:
        raise EnvironmentLockError(str(exc)) from exc


def validate_package(metadata: dict[str, Any], name: str, version: str) -> None:
    info = metadata["info"]
    if canonicalize_name(info.get("name", "")) != name or Version(
        info.get("version", "0")
    ) != Version(version):
        raise ContractError("package metadata differs from the requested exact pin")
    if "3.12.0" not in SpecifierSet(info.get("requires_python") or ""):
        raise EnvironmentLockError("dependency does not support the locked Python version")


def choose_wheel(metadata: dict[str, Any]) -> dict[str, Any]:
    tags = target_tags()
    for asset in sorted(metadata["urls"], key=lambda item: item["filename"]):
        if asset.get("packagetype") != "bdist_wheel" or asset.get("yanked"):
            continue
        name, version, _, wheel_tags = parse_wheel_filename(asset["filename"])
        if name != canonicalize_name(metadata["info"]["name"]) or version != Version(
            metadata["info"]["version"]
        ):
            raise ContractError("wheel filename differs from package metadata")
        if not wheel_tags & tags:
            continue
        if "3.12.0" not in SpecifierSet(asset.get("requires_python") or ""):
            continue
        parts = urlsplit(asset["url"])
        if parts.scheme != "https" or parts.netloc != "files.pythonhosted.org":
            raise ContractError("wheel URL is outside the trusted package host")
        return dict(asset)
    raise EnvironmentLockError("dependency has no supported CPython 3.12 Linux x86_64 wheel")


class WheelSource:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)

    async def _asset(self, client: httpx.AsyncClient, asset: dict[str, Any]) -> bytes:
        size = asset.get("size")
        if isinstance(size, int) and size > self.settings.dependency_max_bytes:
            raise ContractError("dependency wheel exceeds the configured CPU experiment budget")
        if isinstance(size, int):
            try:
                # 每次仍核验公开元数据、闭包和缓存摘要；损坏缓存不能被当作成功结果。
                return await asyncio.to_thread(self.blobs.read, asset["digests"]["sha256"], size)
            except FileNotFoundError:
                pass
        return await read_bounded(
            client,
            asset["url"],
            self.settings.dependency_max_bytes,
            timeout_seconds=self.settings.source_download_timeout_seconds,
        )

    async def fetch(self, spec: EnvironmentSpec) -> tuple[EnvironmentLock, dict[str, bytes]]:
        lock = EnvironmentLock()
        files: dict[str, bytes] = {}
        pins = dict(pin.split("==", 1) for pin in spec.requirements)
        async with httpx.AsyncClient(
            timeout=60,
            trust_env=False,
            proxy=self.settings.source_proxy_url.get_secret_value() or None,
            transport=self.transport,
        ) as client:
            metadata_by_name = {}
            for pin in spec.requirements:
                name, version = pin.split("==", 1)
                metadata = json.loads(
                    await read_bounded(
                        client, f"https://pypi.org/pypi/{name}/{version}/json", 2 * 1024 * 1024
                    )
                )
                validate_package(metadata, name, version)
                metadata_by_name[name] = metadata
            validate_closure(metadata_by_name, pins)
            for pin in spec.requirements:
                name, version = pin.split("==", 1)
                metadata = metadata_by_name[name]
                asset = choose_wheel(metadata)
                raw = await self._asset(client, asset)
                digest = hashlib.sha256(raw).hexdigest()
                if digest != asset["digests"]["sha256"]:
                    raise ContractError("wheel checksum mismatch")
                if (
                    sum(len(value) for value in files.values()) + len(raw)
                    > self.settings.dependency_max_bytes
                ):
                    raise ContractError("combined dependencies exceed configured size limit")
                files[asset["filename"]] = raw
                lock.wheels.append(
                    LockedWheel(
                        requirement=pin,
                        filename=asset["filename"],
                        sha256=digest,
                        size=len(raw),
                        url=asset["url"],
                    )
                )
        return lock, files
