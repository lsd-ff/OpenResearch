from unittest.mock import AsyncMock

import pytest

from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.discovery import SearchRequest, SearchResult
from openresearch.models.planning import SourceSelection
from openresearch.models.runtime import Plan, Task
from openresearch.models.sources import RepositorySnapshot
from openresearch.research.repository_fit import RepositoryFitter
from openresearch.sources.archive import inventory
from openresearch.sources.discovery import SourceDiscovery
from openresearch.sources.github import GitHubSource

NOTEBOOK = "https://github.com/example/notebook"
RUNNABLE = "https://github.com/example/numpy-model"


def snapshot(url, files, ref="HEAD"):
    return RepositorySnapshot(
        url=url, requested_ref=ref, commit="a" * 40, archive_sha256="b" * 64, files=inventory(files)
    )


def plan(goal):
    return Plan(
        user_intent=goal,
        owner_id="alice",
        session_id="fit",
        nodes=[Task(name="fetch", type="repository_fetch", assigned_to="librarian_agent")],
    )


def fitter(settings):
    return RepositoryFitter(
        settings, GitHubSource(settings), SourceDiscovery(), LLMClient(settings)
    )


async def test_notebook_source_searches_real_alternative_before_design(settings):
    agent = fitter(settings)
    original = {"intro.ipynb": b"{}"}
    alternate = {"model.py": b"class ActualModel: pass"}
    agent.llm.request_typed = AsyncMock(
        side_effect=[
            SearchRequest(kind="repository", query="named method numpy"),
            SourceSelection(choice=1, reason="A runnable Python implementation"),
        ]
    )
    agent.discovery.search = AsyncMock(
        return_value=[
            SearchResult(
                title="actual model", url=RUNNABLE, source="github", description="pure NumPy"
            )
        ]
    )
    agent.source.fetch = AsyncMock(return_value=(snapshot(RUNNABLE, alternate), alternate))
    resolved, sources = await agent.fit(
        plan("复现方法的核心并跑通测试"), snapshot(NOTEBOOK, original), original
    )
    assert resolved.url == RUNNABLE and sources == alternate
    request = agent.discovery.search.call_args.args[0]
    assert request.provider == "github" and request.limit == 5
    assert agent.source.fetch.call_args.args[0].url == RUNNABLE


@pytest.mark.parametrize(
    "goal,reference",
    [
        ("只用官方实现", "HEAD"),
        ("复现 " + NOTEBOOK, "HEAD"),
        ("复现核心", "v1"),
    ],
)
async def test_explicit_source_constraints_are_preserved(settings, goal, reference):
    agent = fitter(settings)
    agent.llm.request_typed = AsyncMock(side_effect=AssertionError("must not reselect"))
    files = {"intro.ipynb": b"{}"}
    with pytest.raises(ContractError, match="Notebook"):
        await agent.fit(plan(goal), snapshot(NOTEBOOK, files, reference), files)


async def test_existing_python_source_is_retained_without_new_model_requests(settings):
    agent = fitter(settings)
    agent.llm.request_typed = AsyncMock(side_effect=AssertionError("no new model request"))
    files = {"model.py": b"class ActualModel: pass"}
    original = snapshot(RUNNABLE, files)
    assert await agent.fit(plan("复现核心"), original, files) == (original, files)
