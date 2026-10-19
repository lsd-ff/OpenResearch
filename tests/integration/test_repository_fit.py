from unittest.mock import AsyncMock

import pytest

from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.discovery import SearchRequest, SearchResult
from openresearch.models.planning import RepositoryAssessment, SourceSelection
from openresearch.models.runtime import Plan, Task
from openresearch.models.sources import RepositorySnapshot, RepositorySpec
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
            RepositoryAssessment(covers_architecture=True, reason="Complete requested model"),
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


async def test_compatible_python_source_is_retained_after_actual_source_assessment(settings):
    agent = fitter(settings)
    agent.llm.request_typed = AsyncMock(
        return_value=RepositoryAssessment(
            covers_architecture=True, reason="The source covers the requested architecture"
        )
    )
    agent.discovery.search = AsyncMock(side_effect=AssertionError("no replacement search"))
    files = {"model.py": b"class ActualModel: pass"}
    original = snapshot(RUNNABLE, files)
    assert await agent.fit(plan("复现核心"), original, files) == (original, files)
    assert agent.llm.request_typed.call_args.args[1] is RepositoryAssessment


async def test_decoder_only_source_cannot_satisfy_a_complete_original_architecture(settings):
    agent = fitter(settings)
    original = {"model.py": b"class GPT: pass", "README.md": b"Decoder-only model"}
    alternate = {"model.py": b"class Encoder: pass\nclass Decoder: pass"}
    agent.llm.request_typed = AsyncMock(
        side_effect=[
            RepositoryAssessment(
                covers_architecture=False, reason="Encoder and cross-attention are missing"
            ),
            SearchRequest(kind="repository", query="transformer numpy"),
            SourceSelection(choice=1, reason="Full encoder-decoder implementation"),
            RepositoryAssessment(covers_architecture=True, reason="Complete encoder-decoder"),
        ]
    )
    agent.discovery.search = AsyncMock(
        side_effect=[
            [],
            [
                SearchResult(
                    title="full implementation",
                    url=RUNNABLE,
                    source="github",
                    description="encoder decoder",
                )
            ],
        ]
    )
    agent.source.fetch = AsyncMock(return_value=(snapshot(RUNNABLE, alternate), alternate))
    resolved, _ = await agent.fit(
        plan("复现原始 Transformer 核心"), snapshot(NOTEBOOK, original), original
    )
    assert resolved.url == RUNNABLE
    assert agent.discovery.search.call_args_list[0].args[0].query == "Transformer numpy in:name"
    query_messages = agent.llm.request_typed.call_args_list[1].args[0]
    assert "cross-attention" in query_messages[-1]["content"]


async def test_oversized_candidate_does_not_block_the_next_real_candidate(settings):
    agent = fitter(settings)
    oversized = "https://github.com/example/large-model"
    original = {"intro.ipynb": b"{}"}
    alternate = {"model.py": b"class Encoder: pass\nclass Decoder: pass"}
    agent.llm.request_typed = AsyncMock(
        side_effect=[
            SearchRequest(kind="repository", query="transformer numpy in:name"),
            SourceSelection(choice=1, reason="First actual candidate"),
            SourceSelection(choice=1, reason="Next actual candidate"),
            RepositoryAssessment(covers_architecture=True, reason="Full architecture"),
        ]
    )
    agent.discovery.search = AsyncMock(
        return_value=[
            SearchResult(title="large", url=oversized, source="github", description="large"),
            SearchResult(title="small", url=RUNNABLE, source="github", description="small"),
        ]
    )
    agent.source.fetch = AsyncMock(
        side_effect=[
            ContractError("external source exceeds configured size limit"),
            (snapshot(RUNNABLE, alternate), alternate),
        ]
    )
    resolved, _ = await agent.fit(plan("复现核心"), snapshot(NOTEBOOK, original), original)
    assert resolved.url == RUNNABLE
    assert [call.args[0].url for call in agent.source.fetch.call_args_list] == [oversized, RUNNABLE]
    assert agent.discovery.search.call_count == 1


async def test_failed_initial_automatic_source_can_be_replaced(settings):
    agent = fitter(settings)
    files = {"model.py": b"class ActualModel: pass"}
    agent.llm.request_typed = AsyncMock(
        side_effect=[
            SearchRequest(kind="repository", query="model numpy in:name"),
            SourceSelection(choice=1, reason="Actual replacement"),
            RepositoryAssessment(covers_architecture=True, reason="Full model"),
        ]
    )
    agent.discovery.search = AsyncMock(
        return_value=[
            SearchResult(title="small", url=RUNNABLE, source="github", description="small")
        ]
    )
    agent.source.fetch = AsyncMock(
        side_effect=[ContractError("source unavailable"), (snapshot(RUNNABLE, files), files)]
    )
    resolved, _ = await agent.fetch(plan("复现核心"), RepositorySpec(url=NOTEBOOK))
    assert resolved.url == RUNNABLE


@pytest.mark.parametrize("goal,reference", [("复现 " + NOTEBOOK, "HEAD"), ("复现核心", "v1")])
async def test_failed_fixed_source_never_switches_repositories(settings, goal, reference):
    agent = fitter(settings)
    agent.source.fetch = AsyncMock(side_effect=ContractError("source exceeds budget"))
    agent.llm.request_typed = AsyncMock(side_effect=AssertionError("must preserve source"))
    with pytest.raises(ContractError, match="exceeds budget"):
        await agent.fetch(plan(goal), RepositorySpec(url=NOTEBOOK, ref=reference))


async def test_test_goal_prefers_a_complete_implementation_with_a_real_suite(settings):
    agent = fitter(settings)
    original = {"model.py": b"class CompleteModel: pass"}
    alternate = {
        "model.py": b"class CompleteModel: pass",
        "test_model.py": b"def test_model(): assert True",
    }
    agent.llm.request_typed = AsyncMock(
        side_effect=[
            RepositoryAssessment(covers_architecture=True, reason="Complete original architecture"),
            SourceSelection(choice=1, reason="Same architecture with a documented test suite"),
            RepositoryAssessment(covers_architecture=True, reason="Complete original architecture"),
        ]
    )
    agent.discovery.search = AsyncMock(
        return_value=[
            SearchResult(
                title="tested implementation",
                url=RUNNABLE,
                source="github",
                description="NumPy and pytest",
            )
        ]
    )
    agent.source.fetch = AsyncMock(return_value=(snapshot(RUNNABLE, alternate), alternate))
    resolved, _ = await agent.fit(
        plan("复现 Transformer 核心并跑通测试"), snapshot(NOTEBOOK, original), original
    )
    assert resolved.url == RUNNABLE
    query = agent.discovery.search.call_args.args[0].query
    assert '"test_transformer.py"' in query and "in:readme" in query


async def test_suite_preference_does_not_discard_a_viable_source_during_search_failure(settings):
    agent = fitter(settings)
    original = {"model.py": b"class CompleteModel: pass"}
    agent.llm.request_typed = AsyncMock(
        return_value=RepositoryAssessment(covers_architecture=True, reason="Complete architecture")
    )
    agent.discovery.search = AsyncMock(side_effect=ContractError("search unavailable"))
    source = snapshot(RUNNABLE, original)
    assert await agent.fit(plan("复现 Transformer 核心并测试"), source, original) == (
        source,
        original,
    )


@pytest.mark.parametrize("goal", ["复现 " + RUNNABLE + " 并测试", "复现这篇论文并测试"])
async def test_fixed_or_unnamed_sources_are_not_replaced_to_obtain_a_different_suite(
    settings, goal
):
    agent = fitter(settings)
    original = {"model.py": b"class CompleteModel: pass"}
    agent.llm.request_typed = AsyncMock(
        return_value=RepositoryAssessment(covers_architecture=True, reason="Complete architecture")
    )
    agent.discovery.search = AsyncMock(side_effect=AssertionError("must preserve human selection"))
    source = snapshot(RUNNABLE, original)
    assert await agent.fit(plan(goal), source, original) == (
        source,
        original,
    )
