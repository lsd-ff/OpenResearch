import json

import httpx
import pytest
from pydantic import SecretStr

from openresearch.agents.framework_answers import FrameworkAnswerer
from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.comparison import ComparisonSpec, FrameworkTarget
from openresearch.models.context import Identity
from openresearch.models.execution import ExecutionLease
from openresearch.models.submissions import PlanSubmission
from openresearch.research.framework_protocol import create_protocol
from openresearch.services.plans import PlanService
from openresearch.services.uploads import UploadService


@pytest.mark.parametrize("duplicate", [False, True])
async def test_answers_use_actual_retrieved_context_without_reference_answers(
    settings, repository, files, duplicate
):
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "对比真实框架",
            Identity("alice", "answers"),
            workflow="framework",
            comparison=ComparisonSpec(
                targets=[FrameworkTarget(name="LangChain"), FrameworkTarget(name="LlamaIndex")]
            ),
        )
    )
    result = create_protocol(settings, UploadService(settings, repository), plan, plan.nodes[0])
    lease = ExecutionLease(plan.id, plan.nodes[0].id, "a" * 32, "test")
    plan.artifacts["comparison_protocol"] = files.write(
        lease, "comparison_protocol", result.outputs["comparison_protocol"]
    )
    # 故意让第一行检索到错误文档；生成器必须看到真实错误上下文，不能读取目标纠正。
    predictions = [
        {"id": str(index), "prediction": "d2" if index == 0 else f"d{index + 1}"}
        for index in range(4)
    ]
    plan.artifacts["benchmark_predictions"] = files.write(
        lease, "benchmark_predictions", json.dumps({"baseline": predictions})
    )
    settings.llm_api_key = SecretStr("test-only")
    settings.llm_model = "generation-test"

    def handle(request):
        payload = json.loads(request.content)
        contexts = json.loads(payload["messages"][-1]["content"])
        assert all(set(item) == {"id", "query", "context"} for item in contexts)
        assert "Iris" in contexts[0]["context"] and "Vega" not in contexts[0]["context"]
        answers = [
            {"id": str(index), "answer": answer}
            for index, answer in enumerate(["Iris", "Iris", "Atlas", "Nova"])
        ]
        if duplicate:
            answers[1]["id"] = answers[0]["id"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"answers": answers})}}]}
        )

    answerer = FrameworkAnswerer(
        LLMClient(settings, httpx.MockTransport(handle)), repository, files
    )
    task = next(task for task in plan.nodes if task.type == "framework_answer")
    if duplicate:
        with pytest.raises(ContractError, match="exactly once"):
            await answerer.execute(plan, task)
    else:
        result = await answerer.execute(plan, task)
        report = json.loads(result.outputs["framework_answer_report"])
        assert report["metrics"]["answer_exact_match"] == 0.75
        assert report["generation_seconds"] > 0
