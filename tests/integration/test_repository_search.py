import pytest

from openresearch.errors import ContractError
from openresearch.models.discovery import SearchResult
from openresearch.models.planning import SourceSelection
from openresearch.services.repository_search import RepositorySearch


def candidate(name, source="github"):
    return SearchResult(
        title=name, url="https://github.com/example/" + name, source=source, description=name
    )


async def test_rejected_paper_mapping_continues_to_github_and_keeps_provenance():
    calls = []

    class Discovery:
        async def search(self, request):
            calls.append((request.query, request.provider))
            return (
                [candidate("unrelated-pytorch", "huggingface")]
                if request.provider == "auto"
                else [candidate("transformer")]
            )

    async def select(goal, candidates):
        return SourceSelection(
            choice=1 if candidates[0].title == "transformer" else None, reason="match actual paper"
        )

    results, choice = await RepositorySearch(Discovery(), select).find("Transformer", ["paper"])
    assert choice == 1 and results[0].title == "transformer" and results[0].fallback_used
    assert calls == [("paper", "auto"), ("paper", "github")]


async def test_rejected_nonempty_query_continues_with_human_title():
    calls = []

    class Discovery:
        async def search(self, request):
            calls.append(request.query)
            return [candidate("unrelated" if request.query == "model query" else "transformer")]

    async def select(goal, candidates):
        return SourceSelection(
            choice=1 if candidates[0].title == "transformer" else None, reason="match"
        )

    results, choice = await RepositorySearch(Discovery(), select).find(
        "Transformer", ["model query", "human title"]
    )
    assert choice == 1 and results[0].title == "transformer"
    assert calls == ["model query", "human title"]


async def test_ambiguous_sources_remain_selectable_when_fallback_is_unavailable():
    class Discovery:
        async def search(self, request):
            if request.provider == "github":
                raise ContractError("unavailable")
            return [candidate("ambiguous", "huggingface")]

    async def select(goal, candidates):
        return SourceSelection(choice=None, reason="ambiguous")

    results, choice = await RepositorySearch(Discovery(), select).find("paper", ["query"])
    assert choice is None and [item.title for item in results] == ["ambiguous"]


async def test_all_sources_unavailable_are_not_presented_as_an_empty_success():
    class Discovery:
        async def search(self, request):
            raise ContractError("unavailable")

    async def select(goal, candidates):
        pytest.fail("no candidates must not reach the model")

    with pytest.raises(ContractError, match="unavailable"):
        await RepositorySearch(Discovery(), select).find("paper", ["first", "second"])
