import json

import httpx
import pytest

from openresearch.agents.repository_discovery import RepositoryDiscoverer
from openresearch.models.repository_discovery import (
    RepositoryDiscoveryReport,
    RepositoryDiscoveryRequest,
)
from openresearch.models.runtime import Plan, Task, TaskContract
from openresearch.sources.discovery import SourceDiscovery


def discovery_task(request):
    return Task(
        name="discover",
        type="repository_discover",
        assigned_to="librarian_agent",
        inputs={"discovery": request},
        contract=TaskContract(allowed_tools=["repository.discover"]),
    )


@pytest.mark.parametrize("mode", ["empty", "invalid_url", "unrelated", "fallback", "unavailable"])
async def test_discovery_preserves_actual_empty_untrusted_and_fallback_outcomes(
    settings, files, mode
):
    requests = []

    def handle(request):
        requests.append(request.url.host)
        if mode == "unavailable":
            return httpx.Response(403)
        if request.url.host == "huggingface.co":
            return httpx.Response(
                200,
                json=[
                    {
                        "paper": {
                            "id": "1706.03762",
                            "title": "Unrelated Work",
                            "githubRepo": "https://github.com/example/unrelated",
                        }
                    }
                ],
            )
        items = []
        if mode != "empty":
            items = [
                {
                    "full_name": "example/unrelated" if mode == "unrelated" else "example/demo",
                    "html_url": "https://evil.invalid/repo"
                    if mode == "invalid_url"
                    else "https://github.com/example/"
                    + ("unrelated" if mode == "unrelated" else "demo"),
                }
            ]
        return httpx.Response(200, json={"items": items})

    agent = RepositoryDiscoverer(settings, files)
    agent.discovery = SourceDiscovery(httpx.MockTransport(handle))
    # 此处只去掉测试内的等待；HTTP 契约、真实候选过滤和确定性选择仍由生产代码执行。
    search = agent.discovery.search

    async def immediate(request):
        agent.discovery.last_request = 0
        return await search(request)

    agent.discovery.search = immediate
    task = discovery_task(
        {
            "search": {
                "kind": "repository",
                "query": "demo",
                "provider": "auto" if mode == "fallback" else "github",
            }
        }
    )
    actual = await agent.execute(
        Plan(user_intent="demo", owner_id="alice", session_id="discovery", nodes=[task]), task
    )
    report = RepositoryDiscoveryReport.model_validate_json(actual.outputs["repository_discovery"])
    expected = {
        "empty": "not_found",
        "invalid_url": "not_found",
        "unrelated": "ambiguous",
        "unavailable": "unavailable",
        "fallback": "selected",
    }[mode]
    assert report.status == expected and not report.clone_verified
    assert actual.success and (report.selected is not None) == (mode == "fallback")
    if mode == "fallback":
        assert report.selected.url == "https://github.com/example/demo"
        assert requests == ["huggingface.co", "api.github.com"] and len(report.queries) == 2
    if mode == "unrelated":
        assert (
            json.loads(actual.outputs["candidate_repositories"]) and not actual.outputs["repo_url"]
        )


def test_discovery_declaration_rejects_mismatched_reference():
    with pytest.raises(ValueError):
        RepositoryDiscoveryRequest.model_validate(
            {"ref": "v1", "preferred": {"url": "https://github.com/example/repo", "ref": "main"}}
        )


@pytest.mark.parametrize("archived,fork", [(True, False), (False, True)])
async def test_repository_selection_filters_actual_archived_and_fork_flags(
    settings, files, archived, fork
):
    def handle(request):
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "full_name": "example/demo",
                        "html_url": "https://github.com/example/demo",
                        "stargazers_count": 20000,
                        "archived": archived,
                        "fork": fork,
                    },
                    {
                        "full_name": "example/demo-supported",
                        "html_url": "https://github.com/example/demo-supported",
                        "stargazers_count": 500,
                        "archived": False,
                        "fork": False,
                    },
                ]
            },
        )

    agent = RepositoryDiscoverer(settings, files)
    agent.discovery = SourceDiscovery(httpx.MockTransport(handle))
    task = discovery_task({"search": {"kind": "repository", "query": "demo", "provider": "github"}})
    actual = await agent.execute(
        Plan(user_intent="demo", owner_id="alice", session_id="flags", nodes=[task]), task
    )
    report = RepositoryDiscoveryReport.model_validate_json(actual.outputs["repository_discovery"])
    assert report.selected.url == "https://github.com/example/demo-supported"
    assert len(report.candidates) == 1 and report.candidates[0].search_result.stars == 500


@pytest.mark.parametrize("query,curated", [("Transformer", True), ("Vision Transformer", False)])
async def test_go_curated_reference_is_labeled_declaration_not_search_or_clone(
    settings, files, query, curated
):
    def handle(request):
        if request.url.host == "huggingface.co":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"items": []})

    agent = RepositoryDiscoverer(settings, files)
    agent.discovery = SourceDiscovery(httpx.MockTransport(handle))
    task = discovery_task({"search": {"kind": "repository", "query": query}})
    actual = await agent.execute(
        Plan(user_intent=query, owner_id="alice", session_id="curated", nodes=[task]), task
    )
    report = RepositoryDiscoveryReport.model_validate_json(actual.outputs["repository_discovery"])
    assert bool(report.selected) is curated and not report.clone_verified
    if curated:
        assert report.candidates[0].origin == "curated_fallback"
        assert report.candidates[0].search_result is None and report.candidates[0].fallback_used
        assert "不代表已检索或验证" in report.selection_basis
