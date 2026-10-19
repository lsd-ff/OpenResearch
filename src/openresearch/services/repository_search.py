"""不相关或歧义候选不能阻断其他查询及来源的有限检索。"""

from collections.abc import Awaitable, Callable

from openresearch.errors import ContractError
from openresearch.models.discovery import SearchRequest, SearchResult
from openresearch.models.planning import SourceSelection
from openresearch.sources.discovery import SourceDiscovery

Selector = Callable[[str, list[SearchResult]], Awaitable[SourceSelection]]


class RepositorySearch:
    def __init__(self, discovery: SourceDiscovery, select: Selector) -> None:
        self.discovery = discovery
        self.select = select

    async def find(self, goal: str, queries: list[str]) -> tuple[list[SearchResult], int | None]:
        collected: dict[str, SearchResult] = {}
        available = False
        failure: ContractError | None = None
        for query in queries:
            requests = [SearchRequest(kind="repository", query=query)]
            for request in requests:
                try:
                    candidates = await self.discovery.search(request)
                except ContractError as exc:
                    failure = exc
                    continue
                available = True
                if request.provider == "github":
                    for candidate in candidates:
                        candidate.fallback_used = True
                if candidates:
                    selection = await self.select(goal, candidates)
                    if selection.choice and selection.choice <= len(candidates):
                        return candidates, selection.choice
                collected.update((item.url, item) for item in candidates)
                # 有返回值不等于找到目标论文；主来源被拒绝后仍尝试 GitHub。
                if request.provider == "auto" and any(
                    item.source == "huggingface" for item in candidates
                ):
                    requests.append(
                        SearchRequest(kind="repository", query=query, provider="github")
                    )
        if not available and failure:
            raise failure
        return list(collected.values())[:10], None
