import httpx
import pytest

from openresearch.errors import ContractError
from openresearch.models.discovery import SearchRequest
from openresearch.sources.discovery import SourceDiscovery


def paper(repository: str | None = "https://github.com/example/transformer") -> dict:
    return {
        "id": "1706.03762",
        "title": "Attention Is All You Need",
        "summary": "Original paper metadata",
        "githubRepo": repository,
    }


async def test_repository_discovery_uses_paper_mapping_before_github_search() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.url.host == "huggingface.co"
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json=[{"paper": paper(None)}])
        return httpx.Response(200, json=paper())

    result = await SourceDiscovery(httpx.MockTransport(handle)).search(
        SearchRequest(kind="repository", query="Attention Is All You Need")
    )
    assert len(calls) == 2
    assert result[0].url == "https://github.com/example/transformer"
    assert result[0].source == "huggingface"
    assert result[0].paper_url == "https://arxiv.org/abs/1706.03762"
    assert not result[0].fallback_used


@pytest.mark.parametrize("failure", ["empty", "unavailable", "malformed", "unsafe_repository"])
async def test_discovery_falls_back_to_real_github_results(failure: str) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "huggingface.co":
            if failure == "unavailable":
                return httpx.Response(404)
            values = {
                "empty": [],
                "malformed": {"unexpected": True},
                "unsafe_repository": [{"paper": paper("https://localhost/execute")}],
            }
            return httpx.Response(200, json=values[failure])
        assert request.url.host == "api.github.com"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"full_name": "example/real", "html_url": "https://github.com/example/real"}
                ]
            },
        )

    results = await SourceDiscovery(httpx.MockTransport(handle)).search(
        SearchRequest(kind="repository", query="Transformer")
    )
    assert calls == ["huggingface.co", "api.github.com"]
    assert results[0].source == "github" and results[0].fallback_used


async def test_explicit_provider_does_not_silently_return_other_source() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "huggingface.co"
        return httpx.Response(200, json=[{"paper": paper()}])

    results = await SourceDiscovery(httpx.MockTransport(handle)).search(
        SearchRequest(kind="paper", provider="huggingface", query="attention")
    )
    assert results[0].pdf_url == "https://arxiv.org/pdf/1706.03762"
    assert results[0].source == "huggingface"


async def test_all_source_failures_are_not_reported_as_successful_empty_results() -> None:
    source = SourceDiscovery(httpx.MockTransport(lambda request: httpx.Response(403)))
    with pytest.raises(ContractError, match="unavailable"):
        await source.search(SearchRequest(kind="repository", query="Transformer"))


async def test_untrusted_paper_identifier_cannot_choose_detail_destination() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        value = paper(None)
        value["id"] = "../../private"
        return httpx.Response(200, json=[{"paper": value}])

    with pytest.raises(ContractError, match="identifier"):
        await SourceDiscovery(httpx.MockTransport(handle)).search(
            SearchRequest(kind="repository", provider="huggingface", query="attention")
        )
    assert len(calls) == 1
