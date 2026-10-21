import hashlib
import json

import httpx
from packaging.utils import InvalidName, canonicalize_name
from packaging.version import InvalidVersion, Version

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.sources import PackageDescription
from openresearch.sources.http import read_bounded


class PackageSource:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def describe(self, name: str, version: str | None = None) -> PackageDescription:
        try:
            package = canonicalize_name(name, validate=True)
            pinned = str(Version(version)) if version else None
        except (InvalidName, InvalidVersion):
            raise ContractError("invalid public package identity") from None
        path = package + ("/" + pinned if pinned else "")
        url = f"https://pypi.org/pypi/{path}/json"
        async with httpx.AsyncClient(
            trust_env=False,
            transport=self.transport,
            timeout=30,
            proxy=self.settings.source_proxy_url.get_secret_value() or None,
        ) as client:
            raw = await read_bounded(client, url, 2 * 1024 * 1024)
        try:
            info = json.loads(raw)["info"]
            actual_name, actual_version = info["name"], info["version"]
            if canonicalize_name(actual_name, validate=True) != package or (
                pinned and Version(actual_version) != Version(pinned)
            ):
                raise ValueError("identity mismatch")
            links = info.get("project_urls") or {}
            if not isinstance(links, dict):
                raise ValueError("invalid project links")
            return PackageDescription(
                name=package,
                version=str(Version(actual_version)),
                summary=(info.get("summary") or "")[:2000],
                description=(info.get("description") or "")[:12000],
                requires_python=(info.get("requires_python") or "")[:256],
                project_urls={key[:100]: value[:2000] for key, value in list(links.items())[:10]},
                metadata_url=url,
                metadata_sha256=hashlib.sha256(raw).hexdigest(),
            )
        except (KeyError, ValueError, TypeError):
            raise ContractError(
                "public package metadata has invalid or substituted identity"
            ) from None
