import asyncio
import hashlib
import json
import re
from urllib.parse import quote

import httpx

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.sources import RepositorySnapshot, RepositorySpec
from openresearch.sources.archive import ArchiveLimits, inventory, unpack_repository
from openresearch.sources.http import read_bounded


class GitHubSource:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def fetch(self, spec: RepositorySpec) -> tuple[RepositorySnapshot, dict[str, bytes]]:
        async with httpx.AsyncClient(
            timeout=60,
            trust_env=False,
            transport=self.transport,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "OpenResearch-Python"},
        ) as client:
            # 即使用户给出分支名，也先解析一次完整 SHA，之后不再读取浮动分支。
            commit_url = (
                f"https://api.github.com/repos/{spec.slug}/commits/{quote(spec.ref, safe='')}"
            )
            metadata = json.loads(await read_bounded(client, commit_url, 2 * 1024 * 1024))
            commit = metadata.get("sha")
            if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
                raise ContractError("GitHub returned an invalid commit identity")
            if re.fullmatch(r"[a-f0-9]{40}", spec.ref) and commit != spec.ref:
                raise ContractError("GitHub resolved a different commit")
            url = f"https://codeload.github.com/{spec.slug}/tar.gz/{commit}"
            raw = await read_bounded(client, url, self.settings.source_max_archive_bytes)
        limits = ArchiveLimits(
            self.settings.source_max_bytes,
            self.settings.source_max_file_bytes,
            self.settings.source_max_files,
        )
        files = await asyncio.to_thread(unpack_repository, raw, limits)
        snapshot = RepositorySnapshot(
            url=spec.url,
            requested_ref=spec.ref,
            commit=commit,
            archive_sha256=hashlib.sha256(raw).hexdigest(),
            files=inventory(files),
        )
        return snapshot, files
