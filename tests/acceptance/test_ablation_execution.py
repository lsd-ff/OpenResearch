import json

import httpx
import pytest
from pydantic import SecretStr
from test_docker_smoke import pytestmark as pytestmark
from test_research import benchmark_plan, finish

from openresearch.llm.client import LLMClient
from openresearch.models.ablation import AblationBudget
from openresearch.models.runtime import Status
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker


@pytest.mark.parametrize("unavailable", [False, True])
async def test_selected_ablation_really_changes_predictions_after_preflight(
    settings,
    repository,
    files,
    unavailable,
):
    settings.llm_api_key = SecretStr("test-only")
    settings.llm_model = "test-only"
    settings.llm_schema_retries = 0
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        if unavailable:
            return httpx.Response(503, json={"error": "test-only unavailable model"})
        schema = json.loads(body["messages"][0]["content"].split("\n", 1)[1])
        assert schema["title"] in {"AblationCandidates", "AblationEvaluations"}
        if schema["title"] == "AblationCandidates":
            assert "ablated" in body["messages"][-1]["content"]
            payload = {
                "candidates": [
                    {
                        "id": "ablated",
                        "category": "module",
                        "title": "Disable input feature",
                        "hypothesis": "Predictions change when the actual feature is removed",
                        "change": "Use existing ablated argv branch",
                        "arguments": ["ablated"],
                        "estimated_minutes": 1,
                    }
                ]
            }
        else:
            payload = {
                "evaluations": [
                    {
                        "id": "ablated",
                        "information_gain": 0.9,
                        "relevance": 0.9,
                        "reproducibility": 0.9,
                        "risk": 0.1,
                        "reason": "Direct feature intervention",
                    }
                ]
            }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
        )

    data_agent = worker.executor.agents.agents["data_agent"]
    data_agent.ablations.llm = LLMClient(settings, httpx.MockTransport(provider))
    plan = benchmark_plan(
        settings,
        repository,
        ablations=False,
        search=AblationBudget(max_experiments=1, max_wall_minutes=1),
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        selection = json.loads(files.read(done.artifacts["ablation_plan"]))
        assert len(calls) == 2 and [item["id"] for item in selection["selected"]] == ["ablated"]
        assert selection["generation_source"] == ("source_fallback" if unavailable else "model")
        assert selection["scoring_source"] == ("deterministic_prior" if unavailable else "model")
        report = json.loads(files.read(done.artifacts["research_report"]))
        assert report["ablation_plan"] == selection and report["preflight"]["status"] == "passed"
        assert report["runs"][0]["metrics"]["accuracy"] == 1
        assert report["runs"][1]["variant"] == "ablated"
        assert report["runs"][1]["metrics"]["accuracy"] == pytest.approx(1 / 3)
        assert report["comparison"]["ablated"]["accuracy"] == pytest.approx(-2 / 3)
        assert repository.cleanup_candidates("docker") == []
    finally:
        await worker._shutdown()
