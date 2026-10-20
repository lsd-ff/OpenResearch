import asyncio
import json
from unittest.mock import AsyncMock

from openresearch.errors import DependencyResolutionError
from openresearch.llm.client import LLMClient
from openresearch.models.chat import ExecutionSummary
from openresearch.models.context import Identity
from openresearch.models.planning import ExperimentDesign
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Status
from openresearch.models.submissions import PlanSubmission
from openresearch.scheduler.worker import Worker
from openresearch.services.plans import PlanService


async def test_initial_environment_failure_still_reports_actual_diagnostic_without_running_code(
    settings, repository, files, sandbox, monkeypatch
):
    calls = []

    async def propose(self, messages, contract):
        if contract is ExperimentDesign:
            return ExperimentDesign(dependencies=["invalid==1"], rationale="test source contract")
        assert contract is ExecutionSummary
        context = json.loads(messages[-1]["content"])
        report = json.loads(context["artifacts"]["dependency_resolution_report"])
        assert report["attempts"][0]["exit_code"] == 7
        assert report["attempts"][0]["diagnostic"] == "ACTUAL_PREPARATION_FAILURE"
        assert any(
            item["status"] == "failed" for item in json.loads(context["artifacts"]["task_outcomes"])
        )
        calls.append(context)
        return ExecutionSummary(response="依赖准备失败，未运行科研代码；实际诊断已保存。")

    monkeypatch.setattr(LLMClient, "request_typed", propose)
    worker = Worker(settings, repository, files, sandbox)
    worker.executor.agents.coder.designer.dependencies.resolve = AsyncMock(
        side_effect=DependencyResolutionError("ACTUAL_PREPARATION_FAILURE", 7)
    )
    plan = PlanService(settings, repository).create(
        PlanSubmission(
            "run the provided scientific source",
            Identity("alice", "failed-preparation"),
            code="raise AssertionError('must not run')",
            workflow="reproduce",
            auto_design=True,
            research=ResearchSpec(max_repairs=0),
        )
    )
    try:
        repository.queue_plan(plan.id, "alice")
        async with asyncio.timeout(10):
            while repository.get_plan(plan.id).status not in {Status.COMPLETED, Status.FAILED}:
                await worker.tick()
                await asyncio.sleep(0.01)
        done = repository.get_plan(plan.id)
        assert done.status == Status.FAILED and not sandbox.created and len(calls) == 1
        summary = next(node for node in done.nodes if node.type == "chat_answer")
        assert summary.status == Status.COMPLETED
        assert "未运行科研代码" in files.read(done.artifacts["chat_answer"])
        assert "research_report" not in done.artifacts and "environment_lock" not in done.artifacts
    finally:
        await worker._shutdown()
