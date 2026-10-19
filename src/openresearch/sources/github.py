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
from openresearch.sources.git_refs import advertised_head
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
            proxy=self.settings.source_proxy_url.get_secret_value() or None,
            transport=self.transport,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "OpenResearch-Python"},
        ) as client:
            commit = spec.ref
            if not re.fullmatch(r"[a-f0-9]{40}", commit):
                # 浮动引用必须先固定；完整 SHA 已不可变，无需额外消耗元数据 API 配额。
                commit_url = (
                    f"https://api.github.com/repos/{spec.slug}/commits/{quote(spec.ref, safe='')}"
                )
                try:
                    metadata = json.loads(await read_bounded(client, commit_url, 2 * 1024 * 1024))
                    resolved = metadata.get("sha")
                except ContractError as exc:
                    cause = exc.__cause__
                    limited = isinstance(cause, httpx.HTTPStatusError) and (
                        cause.response.status_code == 429
                        or (
                            cause.response.status_code == 403
                            and cause.response.headers.get("x-ratelimit-remaining") == "0"
                        )
                    )
                    if spec.ref != "HEAD" or not limited:
                        raise
                    # API 配额耗尽时走 Git 官方只读引用发现；仍固定真实 SHA，不猜分支。
                    refs_url = (
                        f"https://github.com/{spec.slug}.git/info/refs?service=git-upload-pack"
                    )
                    resolved = advertised_head(
                        await read_bounded(client, refs_url, 2 * 1024 * 1024)
                    )
                if not isinstance(resolved, str) or not re.fullmatch(r"[a-f0-9]{40}", resolved):
                    raise ContractError("GitHub returned an invalid commit identity")
                commit = resolved
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
