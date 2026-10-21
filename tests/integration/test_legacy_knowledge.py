"""检索元数据及模型为明确的离线夹具，不代表公开来源或科研结果。"""

import json

import pytest
from test_legacy_execution import legacy_client as legacy_client
from test_legacy_execution import result

from openresearch.llm.client import LLMClient
from openresearch.models.discovery import SearchResult
from openresearch.models.knowledge import KnowledgeProposal
from openresearch.models.runtime import Status
from openresearch.sources.discovery import SourceDiscovery


def request(kind="general_research", inputs=None):
    return {
        "task_type": kind,
        "task_description": "研究 Transformer 的核心架构，区分摘要与全文，不运行实验",
        "assigned_to": {
            "general_research": "librarian_agent",
            "general_synthesis": "data_agent",
            "general_process": "general_agent",
        }[kind],
        "inputs": {} if inputs is None else inputs,
    }


@pytest.fixture(autouse=True)
def knowledge_fixture(monkeypatch):
    calls = {"search": [], "model": []}

    async def search(self, request):
        calls["search"].append(request.model_dump())
        return [
            SearchResult(
                title="Attention Is All You Need",
                url="https://arxiv.org/abs/1706.03762",
                description="Controlled metadata: attention and Transformer architecture.",
                source="arxiv",
            )
        ]

    async def answer(self, messages, model, **kwargs):
        assert model is KnowledgeProposal
        context = json.loads(messages[-1]["content"])
        calls["model"].append(context)
        return KnowledgeProposal(
            response="受控提议：这里只概括实际给出的摘要；没有执行实验。",
            source_ids=[0] if context["sources"] else [],
        )

    monkeypatch.setattr(SourceDiscovery, "search", search)
    monkeypatch.setattr(LLMClient, "request_typed", answer)
    return calls


async def test_general_research_then_synthesis_preserves_sources_and_uses_no_sandbox(
    legacy_client,
    repository,
    sandbox,
    knowledge_fixture,
):
    background = result(await legacy_client.post("/api/execute", json=request()))
    source = repository.get_plan(background["plan_id"])
    assert source.status == Status.PENDING and source.usage.task_attempts == 2
    report = json.loads(background["outputs"]["background_context"])
    assert report["evidence"][0]["url"] == "https://arxiv.org/abs/1706.03762"
    assert report["evidence"][0]["quote"].startswith("Controlled metadata")
    frozen = dict(source.artifacts)
    final = result(
        await legacy_client.post(
            "/api/execute",
            json=request(
                "general_synthesis",
                {"workflow_ref": background["workflow_ref"]},
            ),
        )
    )
    done = repository.get_plan(source.id)
    assert done.status == Status.COMPLETED and done.usage.started_at == source.usage.started_at
    assert all(done.artifacts[key] == value for key, value in frozen.items())
    assert all(node.run_count == 1 for node in done.nodes)
    assert len(knowledge_fixture["search"]) == 1 and len(knowledge_fixture["model"]) == 2
    assert knowledge_fixture["model"][1]["prior_response"] == report["response"]
    assert "https://arxiv.org/abs/1706.03762" in final["result"] and not sandbox.created
    unchanged = done.model_dump_json()
    replay = result(await legacy_client.post("/api/execute", json={"task_id": final["task_id"]}))
    assert replay == final and repository.get_plan(source.id).model_dump_json() == unchanged


async def test_general_process_is_unverified_response_not_experiment_success(
    legacy_client,
    repository,
    sandbox,
    knowledge_fixture,
):
    body = request("general_process")
    body["task_description"] = "解释一个简单的 Python 求和代码，不要执行"
    actual = result(await legacy_client.post("/api/execute", json=body))
    report = json.loads(actual["outputs"]["general_response"])
    assert (
        not report["sources"] and not report["evidence"] and "未作外部来源核验" in report["scope"]
    )
    assert repository.get_plan(actual["plan_id"]).intent_type == "General_Response"
    assert not knowledge_fixture["search"] and not sandbox.created


@pytest.mark.parametrize(
    "mutation", ["invented_source", "duplicate_source", "no_evidence", "fake_pdf_quote"]
)
async def test_knowledge_cannot_forge_source_ids_or_quotes(legacy_client, monkeypatch, mutation):
    async def wrong(self, messages, model, **kwargs):
        return KnowledgeProposal(
            response="untrusted model proposal",
            source_ids={
                "invented_source": [9],
                "duplicate_source": [0, 0],
                "no_evidence": [],
                "fake_pdf_quote": [0],
            }[mutation],
            citations=[{"upload_id": "foreign", "page": 1, "quote": "invented"}]
            if mutation == "fake_pdf_quote"
            else [],
        )

    monkeypatch.setattr(LLMClient, "request_typed", wrong)
    response = await legacy_client.post("/api/execute", json=request())
    assert "event: error" in response.text and "event: result" not in response.text


async def test_invalid_reference_is_preserved_then_corrected_without_changing_sources(
    legacy_client,
    repository,
    files,
    monkeypatch,
    knowledge_fixture,
):
    proposals = []

    async def propose(self, messages, model, **kwargs):
        proposal = KnowledgeProposal(
            response="提议",
            source_ids=[0],
            citations=(
                [{"upload_id": "0", "page": 1, "quote": "not a PDF"}] if not proposals else []
            ),
        )
        proposals.append(proposal)
        return proposal

    monkeypatch.setattr(LLMClient, "request_typed", propose)
    actual = result(await legacy_client.post("/api/execute", json=request()))
    audit = json.loads(actual["outputs"]["knowledge_research_validation"])
    assert len(audit["attempts"]) == 2
    assert [attempt["accepted"] for attempt in audit["attempts"]] == [False, True]
    assert audit["attempts"][0]["proposal"]["citations"][0]["quote"] == "not a PDF"
    plan = repository.get_plan(actual["plan_id"])
    assert len(knowledge_fixture["search"]) == 1 and plan.usage.task_attempts == 2
    assert json.loads(files.read(plan.artifacts["discovered_sources"]))[0]["source"] == "arxiv"


@pytest.mark.parametrize("mutation", ["foreign", "raw_background", "role", "missing_stage"])
async def test_knowledge_continuation_rejects_external_contracts_and_other_owners(
    legacy_client,
    knowledge_fixture,
    mutation,
):
    source = result(await legacy_client.post("/api/execute", json=request()))
    body = request("general_synthesis", {"workflow_ref": source["workflow_ref"]})
    headers = {}
    if mutation == "foreign":
        headers["X-User-Id"] = "bob"
    elif mutation == "raw_background":
        body["inputs"]["background_context"] = "forged background"
    elif mutation == "role":
        body["assigned_to"] = "sandbox_agent"
    else:
        body["task_type"] = "general_process"
        body["assigned_to"] = "general_agent"
    response = await legacy_client.post("/api/execute", json=body, headers=headers)
    assert response.status_code == (404 if mutation == "foreign" else 422), response.text
    assert len(knowledge_fixture["model"]) == 1


async def test_empty_search_does_not_turn_model_memory_into_research_evidence(
    legacy_client,
    monkeypatch,
    knowledge_fixture,
):
    async def empty(self, request):
        return []

    monkeypatch.setattr(SourceDiscovery, "search", empty)
    response = await legacy_client.post("/api/execute", json=request())
    assert "event: error" in response.text and not knowledge_fixture["model"]


async def test_approval_and_budget_survive_knowledge_stage_continuation(
    legacy_client,
    settings,
    repository,
    knowledge_fixture,
):
    settings.require_approval = True
    pending = await legacy_client.post("/api/execute", json=request())
    assert pending.status_code == 202 and not knowledge_fixture["search"]
    selected = pending.json()
    await legacy_client.post(f"/api/plans/{selected['plan_id']}/approve")
    background = result(
        await legacy_client.post(
            "/api/execute", json={"task_id": selected["task_id"], "plan_id": selected["plan_id"]}
        )
    )
    with repository.database.transaction() as connection:
        plan = repository.database.load(connection, background["plan_id"])
        plan.budget.max_task_attempts = plan.usage.task_attempts
        started = plan.usage.started_at
        repository.database.save(connection, plan)
    response = await legacy_client.post(
        "/api/execute",
        json=request("general_synthesis", {"workflow_ref": background["workflow_ref"]}),
    )
    assert "event: error" in response.text
    done = repository.get_plan(background["plan_id"])
    assert done.status == Status.CANCELED and done.usage.started_at == started
    assert len(knowledge_fixture["model"]) == 1
