import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openresearch.api.app import create_app
from openresearch.errors import ConflictError, ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.context import ConversationTurn, Identity
from openresearch.models.discovery import SearchResult
from openresearch.models.planning import IntentDraft, SourceSelection
from openresearch.models.submissions import PlanSubmission
from openresearch.services.intent import IntentParser
from openresearch.services.planning_sources import PlanningSources
from openresearch.services.plans import PlanService
from openresearch.sources.discovery import SourceDiscovery
from openresearch.storage.conversations import ConversationUpdate

HEADERS = {"X-User-Id": "alice", "X-Session-Id": "natural-planning"}
URL = "https://github.com/pallets/itsdangerous"


def choose(monkeypatch, **values):
    async def parse(self, snapshot, message, attachments):
        return IntentDraft(**values)

    monkeypatch.setattr(IntentParser, "parse", parse)


@pytest.mark.parametrize(
    "proposal,invalid_query,guessed_url",
    [
        ("", False, None),
        ("Transformer: Attention Is All You Need", False, None),
        ("Transformer: Attention Is All You Need", True, None),
        ("Transformer: Attention Is All You Need", False, "https://github.com/"),
        ("", False, "https://github.com/OWNER/REPO/tree/main"),
    ],
)
def test_named_paper_searches_with_a_human_name_fallback_instead_of_asking_for_urls(
    settings, repository, sandbox, monkeypatch, proposal, invalid_query, guessed_url
):
    choose(monkeypatch, workflow="reproduce", paper_query=proposal, repository_url=guessed_url)
    queries = []
    url = "https://github.com/harvardnlp/annotated-transformer"

    async def search(self, request):
        queries.append(request.query)
        if invalid_query and request.query == proposal:
            raise ContractError("invalid search query")
        return (
            [SearchResult(title="annotated-transformer", url=url, description="", source="github")]
            if request.query == "Transformer"
            else []
        )

    async def select(self, goal, candidates):
        return SourceSelection(choice=1, reason="Matches the requested architecture")

    monkeypatch.setattr(SourceDiscovery, "search", search)
    monkeypatch.setattr(PlanningSources, "_select", select)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/messages",
            json={"message": "复现一下 Transformer 论文的核心架构并跑通测试"},
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["type"] == "plan"
        task = next(
            node for node in payload["plan_graph"]["nodes"] if node["type"] == "repository_fetch"
        )
        assert task["inputs"]["repository"]["url"] == url
        assert queries == ([proposal, "Transformer"] if proposal else ["Transformer"])


def test_empty_search_explains_attempted_queries_without_repeating_a_missing_title_question(
    settings, repository, sandbox, monkeypatch
):
    choose(monkeypatch, workflow="reproduce", paper_query="Transformer: Attention Is All You Need")
    queries = []

    async def search(self, request):
        queries.append(request.query)
        return []

    monkeypatch.setattr(SourceDiscovery, "search", search)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        payload = client.post(
            "/api/messages",
            json={"message": "复现一下 Transformer 论文的核心架构并跑通测试"},
            headers=HEADERS,
        ).json()
        assert payload["type"] == "clarification"
        assert "已检索" in payload["response"] and "Transformer" in payload["response"]
        assert "请提供仓库链接、论文标题或论文附件" not in payload["response"]
        assert len(queries) == 2


@pytest.mark.parametrize("proposed_workflow", ["reproduce", "code"])
def test_paper_reproduction_from_chat_defers_repository_discovery(
    settings, repository, sandbox, monkeypatch, proposed_workflow
):
    choose(monkeypatch, workflow=proposed_workflow)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        uploaded = client.post(
            "/api/uploads",
            files={"file": ("paper.pdf", b"%PDF-1.4\n", "application/pdf")},
            headers=HEADERS,
        )
        assert uploaded.status_code == 201, uploaded.text
        response = client.post(
            "/api/messages",
            json={"message": "复现这篇附件中的协议验收", "attachments": [uploaded.json()["id"]]},
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text
        graph = response.json()["plan_graph"]
        assert len(graph["nodes"]) == 7
        document = next(node for node in graph["nodes"] if node["type"] == "document_extract")
        source = next(node for node in graph["nodes"] if node["type"] == "repository_fetch")
        assert source["dependencies"] == [document["id"]]
        assert "repository" not in source["inputs"]


@pytest.mark.parametrize("model_url", [URL, "https://github.com/"])
def test_repository_reproduction_from_chat_requires_no_form(
    settings, repository, sandbox, monkeypatch, model_url
):
    choose(monkeypatch, workflow="reproduce", repository_url=model_url)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/messages", json={"message": f"用 {URL} 做轻量复现实验"}, headers=HEADERS
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["type"] == "plan"
        graph = payload["plan_graph"]
        assert graph["intent_type"] == "Paper_Reproduction"
        assert {node["type"] for node in graph["nodes"]} >= {
            "repository_fetch",
            "research_design",
            "environment_lock",
            "claim_freeze",
            "research_prepare",
            "research_execute",
        }
        assert (
            next(node for node in graph["nodes"] if node["type"] == "repository_fetch")["inputs"][
                "repository"
            ]["url"]
            == URL
        )
        assert (
            repository.sessions.get(Identity("alice", "natural-planning")).pending_planning is None
        )


def test_explicit_http_repository_still_requires_a_supported_source(
    settings, repository, sandbox, monkeypatch
):
    url = "http://github.com/pallets/itsdangerous"
    choose(monkeypatch, workflow="reproduce", repository_url=url)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        payload = client.post(
            "/api/messages", json={"message": f"复现 {url}"}, headers=HEADERS
        ).json()
        assert payload["type"] == "clarification" and "HTTPS GitHub" in payload["response"]
        assert not repository.sessions.get(Identity("alice", "natural-planning")).plans


def test_missing_material_question_survives_restart_and_url_reply(
    settings, repository, sandbox, monkeypatch
):
    choose(monkeypatch, workflow="reproduce")
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/messages", json={"message": "帮我复现这个实验"}, headers=HEADERS
        ).json()
        assert response["type"] == "clarification" and "仓库" in response["response"]
        assert "plan_graph" not in response
    assert (
        repository.sessions.get(Identity("alice", "natural-planning")).pending_planning is not None
    )
    choose(monkeypatch, workflow="reproduce", repository_url=URL)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post("/api/messages", json={"message": URL}, headers=HEADERS).json()
        assert response["type"] == "plan"
        assert "帮我复现这个实验" in response["plan_graph"]["user_intent"]
        other = client.get(
            "/api/session", headers={"X-User-Id": "bob", "X-Session-Id": "natural-planning"}
        ).json()
        assert other["pending_planning"] is None and not other["plans"]


def test_benchmark_automatically_profiles_columns_without_form(
    settings, repository, sandbox, monkeypatch
):
    choose(monkeypatch, workflow="benchmark", repository_url=URL)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        upload = client.post(
            "/api/uploads",
            files={
                "file": ("dataset.csv", io.BytesIO(b"id,x,label\na,1,yes\nb,0,no\n"), "text/csv")
            },
            headers=HEADERS,
        ).json()
        response = client.post(
            "/api/messages",
            json={"message": f"用 {URL} 在上传数据上跑 benchmark", "attachments": [upload["id"]]},
            headers=HEADERS,
        ).json()
        assert response["type"] == "plan"
        design = next(
            node for node in response["plan_graph"]["nodes"] if node["type"] == "research_design"
        )
        benchmark = design["inputs"]["research"]["benchmark"]
        assert benchmark["input_columns"] == ["x"] and benchmark["target_column"] == "label"
        assert benchmark["id_column"] == "id"
        assert not response["plan_graph"]["attachments"], (
            "targets must not enter source-document context"
        )


def test_ambiguous_target_is_asked_and_short_reply_resumes(
    settings, repository, sandbox, monkeypatch
):
    choose(monkeypatch, workflow="benchmark", repository_url=URL)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        upload = client.post(
            "/api/uploads",
            files={"file": ("dataset.csv", b"text,sentiment,score\na,yes,1\nb,no,0\n", "text/csv")},
            headers=HEADERS,
        ).json()
        first = client.post(
            "/api/messages",
            json={"message": f"用 {URL} 跑 benchmark", "attachments": [upload["id"]]},
            headers=HEADERS,
        ).json()
        assert first["type"] == "clarification" and "目标" in first["response"]
        choose(
            monkeypatch,
            workflow="benchmark",
            repository_url=URL,
            target_column="sentiment",
            input_columns=["text"],
        )
        second = client.post(
            "/api/messages", json={"message": "标签是 sentiment，输入是 text"}, headers=HEADERS
        ).json()
        assert second["type"] == "plan"
        assert "标签是 sentiment" in second["plan_graph"]["user_intent"]


def test_mentioned_input_column_is_not_accepted_as_an_invented_target(
    settings, repository, sandbox, monkeypatch
):
    choose(
        monkeypatch,
        workflow="benchmark",
        repository_url=URL,
        target_column="tampered",
        paper_url="https://arxiv.org/abs/0000.00000",
        dataset_name="invented.csv",
        input_columns=["invented_feature"],
    )
    with TestClient(create_app(settings, repository, sandbox)) as client:
        upload = client.post(
            "/api/uploads",
            files={"file": ("data.csv", b"id,tampered,verdict\na,0,yes\nb,1,no\n", "text/csv")},
            headers=HEADERS,
        ).json()
        payload = client.post(
            "/api/messages",
            json={
                "message": f"用 {URL} 跑 benchmark，tampered 为 1 时篡改签名",
                "attachments": [upload["id"]],
            },
            headers=HEADERS,
        ).json()
        assert payload["type"] == "clarification" and "目标" in payload["response"]
        assert not repository.sessions.get(Identity("alice", "natural-planning")).plans


def test_identifier_cannot_disclose_target_labels():
    from openresearch.models.research import BenchmarkSpec

    with pytest.raises(ValueError, match="identifiers"):
        BenchmarkSpec(upload_id="data", input_columns=["x"], target_column="y", id_column="y")


def test_invented_repository_is_not_executed(settings, repository, sandbox, monkeypatch):
    choose(monkeypatch, workflow="reproduce", repository_url=URL)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/messages", json={"message": "复现一个不存在的论文"}, headers=HEADERS
        ).json()
        assert response["type"] == "clarification" and "模型推测" in response["response"]
        assert not repository.sessions.get(Identity("alice", "natural-planning")).plans


def test_stale_conversation_does_not_insert_an_orphan_plan(settings, repository):
    identity = Identity("alice", "natural-planning")
    snapshot = repository.sessions.get(identity)
    user = ConversationTurn(role="user", content="smoke")
    assistant = ConversationTurn(role="assistant", content="created")
    first = PlanService(settings, repository).build(PlanSubmission("smoke", identity))
    second = PlanService(settings, repository).build(PlanSubmission("smoke", identity))
    repository.sessions.publish(ConversationUpdate(snapshot, user, assistant, None, first))
    with pytest.raises(ConflictError):
        repository.sessions.publish(ConversationUpdate(snapshot, user, assistant, None, second))
    assert repository.sessions.get(identity).plans == [first.id]
    from openresearch.errors import NotFoundError

    with pytest.raises(NotFoundError):
        repository.get_plan(second.id)


def test_legacy_auto_plan_endpoint_uses_conversation_planning(
    settings, repository, sandbox, monkeypatch
):
    choose(monkeypatch, workflow="reproduce", repository_url=URL)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post("/api/plan", json={"intent": f"复现 {URL}"}, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["plan_graph"]["intent_type"] == "Paper_Reproduction"


@pytest.mark.parametrize("suffix,expected", [("", "HEAD"), ("使用 main 分支", "main")])
def test_repository_version_uses_human_choice_or_actual_default(
    settings, repository, sandbox, monkeypatch, suffix, expected
):
    choose(monkeypatch, workflow="reproduce", repository_url=URL, repository_ref="main")
    with TestClient(create_app(settings, repository, sandbox)) as client:
        payload = client.post(
            "/api/messages", json={"message": f"复现 {URL} {suffix}"}, headers=HEADERS
        ).json()
        assert payload["type"] == "plan"
        task = next(
            node for node in payload["plan_graph"]["nodes"] if node["type"] == "repository_fetch"
        )
        assert task["inputs"]["repository"]["ref"] == expected


async def test_explicit_benchmark_cannot_be_downgraded_by_model(settings, repository):
    settings.llm_api_key = SecretStr("test-token")
    settings.llm_model = "test"
    parser = IntentParser(settings)
    parser.llm = LLMClient(
        settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps({"workflow": "reproduce"})}}]},
            )
        ),
    )
    snapshot = repository.sessions.get(Identity("alice", "classify"))
    result = await parser.parse(snapshot, "用仓库跑 benchmark", [])
    assert result.workflow == "benchmark"
    result = await parser.parse(snapshot, "对比 LangChain 和 LlamaIndex 的 RAG 性能", [])
    assert result.workflow == "benchmark"
    result = await parser.parse(snapshot, "无需 benchmark，只跑仓库冒烟实验", [])
    assert result.workflow == "reproduce"


def test_paper_download_failure_keeps_a_material_question(
    settings, repository, sandbox, monkeypatch
):
    from openresearch.errors import ContractError
    from openresearch.services.planning_sources import PlanningSources

    choose(monkeypatch, workflow="paper", paper_url="https://arxiv.org/abs/1706.03762")

    async def unavailable(self, snapshot, url):
        raise ContractError("external unavailable")

    monkeypatch.setattr(PlanningSources, "download", unavailable)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/messages",
            json={"message": "阅读 https://arxiv.org/abs/1706.03762"},
            headers=HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["type"] == "clarification"
        assert (
            repository.sessions.get(Identity("alice", "natural-planning")).pending_planning
            is not None
        )
