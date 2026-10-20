import asyncio
import json
import os

import pytest
from test_docker_smoke import pytestmark as docker_marks

from openresearch.models.comparison import ComparisonSpec, FrameworkTarget
from openresearch.models.context import Identity
from openresearch.models.runtime import Status
from openresearch.models.submissions import PlanSubmission
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker
from openresearch.services.plans import PlanService

pytestmark = [
    *docker_marks,
    pytest.mark.external,
    pytest.mark.skipif(
        os.environ.get("OPENRESEARCH_TEST_EXTERNAL") != "1",
        reason="Real framework dependency downloads require explicit external opt-in",
    ),
]


async def test_actual_langchain_and_llamaindex_share_data_but_not_environments(
    settings, repository, files
):
    settings.task_timeout_seconds = 600
    settings.plan_timeout_seconds = 1800
    plan = PlanService(settings, repository).create(
        PlanSubmission(
            "对比 LangChain 和 LlamaIndex 的实际检索",
            Identity("libraries", "real"),
            workflow="framework",
            comparison=ComparisonSpec(
                answer_mode="none",
                targets=[
                    FrameworkTarget(name="LangChain"),
                    FrameworkTarget(name="LlamaIndex"),
                ],
            ),
        )
    )
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    repository.queue_plan(plan.id, plan.owner_id)
    try:
        async with asyncio.timeout(1800):
            while True:
                await worker.tick()
                done = repository.get_plan(plan.id)
                if done.status in {Status.COMPLETED, Status.FAILED}:
                    break
                await asyncio.sleep(0.05)
        await asyncio.gather(*(active.future for active in worker.active.values()))
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        result = json.loads(files.read(done.artifacts["comparison_metrics"]))
        assert result["complete"] and result["protocol"]["origin"] == "synthetic"
        for row, method in zip(
            result["frameworks"], ["similarity_search", "retrieve"], strict=True
        ):
            assert row["dataset_sha256"] == result["protocol"]["dataset_sha256"]
            report = row["report"]
            assert report["preflight"]["status"] == "passed"
            assert report["runs"][0]["execution_seconds"] > 0
            assert any(
                call.endswith("." + method)
                for calls in report["runs"][0]["module_calls"].values()
                for call in calls
            )
            assert row["metrics"]["framework_module_usage"] == 1
            assert row["metrics"]["prediction_coverage"] == 1
        locks = [row["report"]["environment_lock_sha256"] for row in result["frameworks"]]
        assert locks[0] != locks[1]
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()
