import json

import httpx
import pytest
from pydantic import SecretStr

from openresearch.agents.framework_research import FrameworkResearcher
from openresearch.llm.client import LLMClient
from openresearch.models.runtime import Task, TaskContract
from openresearch.models.sources import PackageDescription


@pytest.mark.parametrize("source_id", [0, 999])
async def test_research_model_selects_actual_source_and_cannot_rewrite_quote(
    settings, monkeypatch, source_id
):
    settings.llm_api_key = SecretStr("test-only")
    settings.llm_model = "source-test"
    agent = FrameworkResearcher(settings)
    package = PackageDescription(
        name="langchain-core",
        version="1.0",
        summary="Actual source quote.",
        metadata_url="https://pypi.org/pypi/langchain-core/1.0/json",
        metadata_sha256="a" * 64,
    )

    async def describe(target):
        return [package]

    monkeypatch.setattr(agent.sources, "describe", describe)

    def handle(request):
        supplied = json.loads(json.loads(request.content)["messages"][-1]["content"])
        assert supplied["sources"][0]["quote"] == package.summary
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "response": "来源支持的适用性分析。",
                                    "source_ids": [source_id],
                                }
                            )
                        }
                    }
                ]
            },
        )

    agent.sources.llm = LLMClient(settings, httpx.MockTransport(handle))
    task = Task(
        name="research",
        type="framework_research",
        assigned_to="librarian_agent",
        inputs={"target": {"name": "LangChain"}},
        contract=TaskContract(allowed_tools=["framework.research"]),
    )
    result = await agent.execute(task, "研究框架适用性")
    report = json.loads(result.outputs["framework_research_report"])
    assert result.success is (source_id == 0)
    if result.success:
        assert report["fit"]["evidence"][0]["quote"] == package.summary
    else:
        assert report["fit"] is None and "unknown" in result.error
        assert report["packages"][0]["metadata_sha256"] == "a" * 64
