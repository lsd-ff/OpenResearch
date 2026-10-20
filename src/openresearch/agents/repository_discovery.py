"""确定性仓库发现只选择真实候选；来源排名不是实现正确或实验成功的证明。"""

import asyncio
import json
import re

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.discovery import SearchRequest
from openresearch.models.documents import PaperCorpus, SourceIndex
from openresearch.models.repository_discovery import (
    RepositoryCandidate,
    RepositoryDiscoveryReport,
    RepositoryDiscoveryRequest,
)
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.models.sources import RepositorySpec
from openresearch.services.source_queries import GENERIC_TERMS, named_source_query
from openresearch.sources.discovery import SourceDiscovery
from openresearch.storage.files import ArtifactStore


def paper_repositories(plan: Plan, files: ArtifactStore, ref: str) -> list[RepositoryCandidate]:
    if "source_index" not in plan.artifacts:
        return []
    index = SourceIndex.model_validate_json(files.read(plan.artifacts["source_index"]))
    candidates = {}
    for link in index.links:
        try:
            repository = RepositorySpec(url=link.url, ref=ref)
        except ValueError:
            continue
        candidates[repository.url] = RepositoryCandidate(
            repository=repository,
            title=repository.slug,
            origin="paper_link",
            upload_id=link.upload_id,
            page=link.page,
            description="实际文档中提取的 GitHub 链接，尚未验证可下载性。",
        )
        if len(candidates) == 10:
            break
    return list(candidates.values())


def paper_queries(plan: Plan, files: ArtifactStore) -> list[str]:
    if "parsed_paper" not in plan.artifacts:
        return []
    corpus = PaperCorpus.model_validate_json(files.read(plan.artifacts["parsed_paper"]))
    queries = []
    for document in corpus.documents:
        text = "\n".join(page.text[:3000] for page in document.pages[:2])
        identifier = re.search(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b", text)
        title = document.title.strip()
        if not title or title.lower().endswith(".pdf"):
            title = next((line.strip() for line in text.splitlines() if line.strip()), "")
        query = identifier[0] if identifier else title[:240]
        if query and query not in queries:
            queries.append(query)
    return queries[:2]


def ranked(candidates: list[RepositoryCandidate], query: str) -> list[RepositoryCandidate]:
    words = set(re.findall(r"[a-z0-9][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", query.casefold()))
    words -= GENERIC_TERMS | {
        "user",
        "repo",
        "name",
        "language",
        "the",
        "and",
        "for",
        "find",
        "search",
    }
    for candidate in candidates:
        paper = candidate.search_result.paper_url if candidate.search_result else ""
        text = " ".join(
            [candidate.title, candidate.repository.slug, candidate.description, paper or ""]
        ).casefold()
        candidate.matching_terms = sum(word in text for word in words)
        candidate.score = 10 * candidate.matching_terms
        if candidate.origin in {"paper_link", "huggingface", "curated_fallback"}:
            candidate.score += 3
        if candidate.search_result and candidate.search_result.stars is not None:
            stars = candidate.search_result.stars
            candidate.score += (
                12 if stars >= 10000 else 8 if stars >= 3000 else 4 if stars >= 500 else 0
            )
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def relevant(candidate: RepositoryCandidate) -> bool:
    return candidate.origin == "paper_link" or candidate.matching_terms > 0


def curated_reference(query: str, ref: str) -> list[RepositoryCandidate]:
    # 保留 Go 自带的原始 Transformer 参考声明；不把它当成本次检索结果或替代其他变体。
    normalized = " ".join(dict.fromkeys(query.casefold().split()))
    if normalized != "transformer" and "attention is all you need" not in normalized:
        return []
    return [
        RepositoryCandidate(
            repository=RepositorySpec(
                url="https://github.com/harvardnlp/annotated-transformer", ref=ref
            ),
            title="Attention Is All You Need",
            origin="curated_fallback",
            fallback_used=True,
            description="Go 版本内置参考声明；本次未检索到匹配来源，下载、提交及实现仍待验证。",
        )
    ]


class RepositoryDiscoverer:
    def __init__(self, settings: Settings, files: ArtifactStore) -> None:
        self.files = files
        self.discovery = SourceDiscovery(proxy=settings.source_proxy_url.get_secret_value() or None)

    async def execute(self, plan: Plan, task: Task) -> TaskResult:
        if "repository.discover" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to discover repository sources")
        request = RepositoryDiscoveryRequest.model_validate(task.inputs["discovery"])
        if request.preferred:
            report = RepositoryDiscoveryReport(
                status="selected",
                selected=request.preferred,
                candidates=[
                    RepositoryCandidate(
                        repository=request.preferred,
                        title=request.preferred.slug,
                        origin="user_preference",
                    )
                ],
                selection_basis="用户明确声明的仓库；仅校验地址格式，下载由后续阶段验证。",
            )
        else:
            report = await self.collect(plan, request)
        raw = report.model_dump_json()
        selected = report.selected.url if report.selected else "未发现可用的仓库候选。"
        return TaskResult(
            result=selected if report.selected else selected + "\n\n" + report.scope,
            structured_data=raw,
            outputs={
                "repository_discovery": raw,
                "candidate_repositories": json.dumps(
                    [
                        {
                            "title": candidate.title,
                            "repo_name": candidate.repository.slug,
                            "description": candidate.description,
                            "repo_urls": [candidate.repository.url],
                            "source": candidate.origin,
                            "score_hint": candidate.score,
                            "fallback_used": candidate.fallback_used,
                        }
                        for candidate in report.candidates
                    ],
                    ensure_ascii=False,
                ),
                "repo_validation_report": raw,
                "repo_url": report.selected.url if report.selected else "",
            },
        )

    async def collect(
        self, plan: Plan, request: RepositoryDiscoveryRequest
    ) -> RepositoryDiscoveryReport:
        candidates = await asyncio.to_thread(paper_repositories, plan, self.files, request.ref)
        queries = (
            [request.search]
            if request.search
            else [
                SearchRequest(kind="repository", query=query)
                for query in (
                    await asyncio.to_thread(paper_queries, plan, self.files)
                    or [named_source_query(plan.user_intent) or plan.user_intent[:240]]
                )
            ]
        )
        report = RepositoryDiscoveryReport(status="not_found")
        available = bool(candidates)
        for query in queries if not candidates else []:
            report.queries.append(query)
            try:
                results = await self.discovery.search(query)
                available = True
            except ContractError as exc:
                report.errors.append(str(exc)[:500])
                continue
            for result in results:
                try:
                    repository = RepositorySpec(url=result.url, ref=request.ref)
                except ValueError:
                    continue
                if result.source not in {"github", "huggingface"}:
                    continue
                if result.archived is True or result.fork is True:
                    continue
                candidates.append(
                    RepositoryCandidate(
                        repository=repository,
                        title=result.title[:500],
                        description=result.description[:4000],
                        origin=result.source,
                        search_result=result,
                        fallback_used=result.fallback_used,
                    )
                )
            ranked(candidates, query.query)
            if any(relevant(candidate) for candidate in candidates):
                break
            if (
                query.provider == "auto"
                and any(item.origin == "huggingface" for item in candidates)
                and len(queries) < 2
            ):
                queries.append(query.model_copy(update={"provider": "github"}))
        query_text = " ".join(query.query for query in report.queries) or plan.user_intent
        if not any(relevant(candidate) for candidate in candidates):
            candidates.extend(curated_reference(query_text, request.ref))
        report.candidates = ranked(
            list({item.repository.url: item for item in candidates}.values()), query_text
        )[:10]
        matches = [candidate for candidate in report.candidates if relevant(candidate)]
        if matches:
            report.selected = matches[0].repository
            report.status = "selected"
            report.selection_basis = (
                "实际来源优先并按查询词重合排序，选择最高排名候选；不保证实现完整性。"
            )
            if matches[0].origin == "curated_fallback":
                report.selection_basis = (
                    "本次来源没有匹配结果，采用 Go 内置参考声明；不代表已检索或验证仓库。"
                )
        elif report.candidates:
            report.status = "ambiguous"
            report.selection_basis = "保留实际候选，但没有足够查询词对应证据，不能自动指定仓库。"
        elif not available:
            report.status = "unavailable"
        return report
