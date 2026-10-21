"""候选/归档与模型提议为明确夹具；PDF 解析、文件校验及程序均在真实容器执行。"""

import asyncio
import hashlib
import json

import httpx
import pytest
from conftest import make_repository_archive
from test_docker_smoke import pytestmark as pytestmark
from test_documents import real_pdf
from test_legacy_api import completed_result
from test_legacy_api import legacy_docker as legacy_docker

from openresearch.llm.client import LLMClient
from openresearch.models.discovery import SearchResult
from openresearch.models.knowledge import KnowledgeProposal
from openresearch.models.runtime import Status
from openresearch.sources.discovery import SourceDiscovery
from openresearch.sources.github import GitHubSource

CODE = (
    "import main\nfrom pathlib import Path\n"
    "assert Path('main.py').read_text() == 'print(1)'\n"
    "assert 2 + 3 == 5\nprint('DISCOVERY_ACTUAL', 5)\n"
)


@pytest.fixture
async def counted_docker(legacy_docker, monkeypatch):
    client, sandbox = legacy_docker
    created = []
    original = sandbox.create

    async def create(plan_id, execution_id):
        identifier = await original(plan_id, execution_id)
        created.append(identifier)
        return identifier

    monkeypatch.setattr(sandbox, "create", create)
    yield client, sandbox, created


def request(kind, inputs):
    role = (
        "coder_agent"
        if kind in {"repo_discovery", "repo_prepare", "paper_code_execute"}
        else "librarian_agent"
        if kind == "general_research"
        else "data_agent"
    )
    return {
        "task_type": kind,
        "assigned_to": role,
        "task_description": "读取原始来源并逐阶段处理，保留代码和断言，不要自动修复",
        "inputs": inputs,
    }


@pytest.fixture(autouse=True)
def controlled_sources(monkeypatch):
    calls = {"search": [], "model": []}

    async def search(self, query):
        calls["search"].append(query.query)
        if query.query == "absent":
            return []
        return [
            SearchResult(
                title="example/demo",
                url="https://github.com/example/demo",
                description="demo source fixture",
                source="github",
            )
        ]

    def provider(request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"sha": "a" * 40})
        assert request.url.host == "codeload.github.com" and request.url.path.endswith("a" * 40)
        return httpx.Response(200, content=make_repository_archive())

    initialize = GitHubSource.__init__

    def github_init(self, settings, transport=None):
        initialize(self, settings, httpx.MockTransport(provider))

    async def answer(self, messages, model, **kwargs):
        assert model is KnowledgeProposal
        data = json.loads(messages[-1]["content"])
        calls["model"].append(data)
        page = next(page for page in data["pages"] if "Research result is 0.75." in page["text"])
        return KnowledgeProposal(
            response="受控提议：原文写有 0.75，第二页缺少可提取文本；未进行新实验。",
            citations=[
                {
                    "upload_id": page["upload_id"],
                    "page": page["page"],
                    "quote": "Research result is 0.75.",
                }
            ],
        )

    monkeypatch.setattr(SourceDiscovery, "search", search)
    monkeypatch.setattr(GitHubSource, "__init__", github_init)
    monkeypatch.setattr(LLMClient, "request_typed", answer)
    return calls


async def test_discovery_preparation_and_real_program_share_one_pinned_graph(
    counted_docker,
    repository,
    controlled_sources,
):
    client, sandbox, created = counted_docker
    async with asyncio.timeout(180):
        source = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "repo_discovery",
                    {
                        "search": {"kind": "repository", "query": "demo", "provider": "github"},
                        "code": CODE,
                        "research": {"max_repairs": 0},
                    },
                ),
            )
        )
        before = repository.get_plan(source["plan_id"])
        assert before.usage.task_attempts == 1 and not created
        prepared = completed_result(
            await client.post(
                "/api/execute",
                json=request("repo_prepare", {"workflow_ref": source["workflow_ref"]}),
            )
        )
        assert json.loads(prepared["outputs"]["repo_manifest"])["commit"] == "a" * 40
        assert prepared["code"] == CODE
        partial = repository.get_plan(before.id)
        assert "research_report" not in partial.artifacts
        executed = completed_result(
            await client.post(
                "/api/execute",
                json=request("paper_code_execute", {"workflow_ref": source["workflow_ref"]}),
            )
        )
        report = json.loads(executed["outputs"]["research_report"])
        assert report["success"] and report["repository_commit"] == "a" * 40
        assert report["runs"][0]["attempts"][-1]["stdout"] == "1\nDISCOVERY_ACTUAL 5\n"
        assert (
            report["source_integrity"]["provided_code_sha256"]
            == hashlib.sha256(CODE.encode()).hexdigest()
        )
        done = repository.get_plan(before.id)
        assert done.status == Status.COMPLETED and all(task.run_count == 1 for task in done.nodes)
        assert done.usage.started_at == before.usage.started_at and controlled_sources[
            "search"
        ] == ["demo"]
        assert len(created) == 2
    assert not await sandbox.inventory()


async def test_missing_selection_blocks_fetch_and_environment_before_any_program(
    counted_docker,
    repository,
):
    client, sandbox, created = counted_docker
    async with asyncio.timeout(60):
        source = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "repo_discovery",
                    {"search": {"kind": "repository", "query": "absent"}, "code": CODE},
                ),
            )
        )
        assert not source["outputs"]["repo_url"]
        failed = await client.post(
            "/api/execute", json=request("repo_prepare", {"workflow_ref": source["workflow_ref"]})
        )
        assert "event: error" in failed.text
        plan = repository.get_plan(source["plan_id"])
        assert all(
            node.run_count == 0
            for node in plan.nodes
            if node.type in {"environment_lock", "research_execute"}
        )
        assert not created
    assert not await sandbox.inventory()


async def test_pdf_research_and_synthesis_keep_actual_quotes_sha_and_no_experiment(
    legacy_docker,
    repository,
    controlled_sources,
):
    client, sandbox = legacy_docker
    raw = real_pdf()
    uploaded = await client.post(
        "/api/uploads", files={"file": ("knowledge.pdf", raw, "application/pdf")}
    )
    async with asyncio.timeout(90):
        source = completed_result(
            await client.post(
                "/api/execute",
                json=request("general_research", {"attachments": [uploaded.json()["id"]]}),
            )
        )
        background = json.loads(source["outputs"]["background_context"])
        assert background["documents"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
        assert background["citations"][0]["quote"] == "Research result is 0.75."
        final = completed_result(
            await client.post(
                "/api/execute",
                json=request("general_synthesis", {"workflow_ref": source["workflow_ref"]}),
            )
        )
        report = json.loads(final["outputs"]["general_response"])
        assert (
            report["citations"] == background["citations"]
            and report["documents"] == background["documents"]
        )
        plan = repository.get_plan(source["plan_id"])
        assert plan.status == Status.COMPLETED and len(plan.nodes) == 3
        assert all(node.run_count == 1 for node in plan.nodes)
        assert not controlled_sources["search"] and len(controlled_sources["model"]) == 2
    assert not await sandbox.inventory()


async def test_repository_link_is_read_from_actual_pdf_before_any_clone(
    legacy_docker,
    controlled_sources,
):
    client, sandbox = legacy_docker
    uploaded = await client.post(
        "/api/uploads", files={"file": ("paper.pdf", real_pdf(), "application/pdf")}
    )
    async with asyncio.timeout(90):
        source = completed_result(
            await client.post(
                "/api/execute",
                json=request("repo_discovery", {"attachments": [uploaded.json()["id"]]}),
            )
        )
        report = json.loads(source["outputs"]["repository_discovery"])
        assert report["selected"]["url"] == "https://github.com/example/research"
        assert (
            report["candidates"][0]["page"] == 1
            and report["candidates"][0]["upload_id"] == uploaded.json()["id"]
        )
        assert not report["clone_verified"] and not controlled_sources["search"]
    assert not await sandbox.inventory()
