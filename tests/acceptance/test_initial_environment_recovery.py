import json
import os
from unittest.mock import AsyncMock

import pytest
from test_benchmark_preflight import COUNTED, DATA
from test_docker_smoke import pytestmark as pytestmark
from test_research import PREDICTOR, benchmark_plan, finish

from openresearch.models.chat import ExecutionSummary
from openresearch.models.environment_repair import EnvironmentRepairProposal
from openresearch.models.planning import ExperimentDesign
from openresearch.models.runtime import Status
from openresearch.models.sources import EnvironmentSpec
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker


@pytest.mark.external
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="PyPI opt-in required")
@pytest.mark.parametrize("stage", ["resolve", "lock"])
async def test_actual_initial_dependency_failure_recovers_then_executes_real_benchmark(
    settings, repository, files, stage
):
    # 模型设计/提议为夹具；uv、PyPI、wheel 校验、安装、程序与指标均实际运行。
    predictor = PREDICTOR.replace("print('real predictions written')", COUNTED)
    code = (
        "import yaml\nvalue = yaml.safe_load('number: 17')\nassert value['number'] == 17\n"
        "print('ACTUAL_INITIAL_RECOVERY', yaml.__version__, yaml.__file__, value['number'])\n"
        if stage == "resolve"
        else "import requests\nprepared = requests.Request('GET', 'https://example.com/').prepare()\n"
        "assert prepared.url == 'https://example.com/'\n"
        "print('ACTUAL_INITIAL_RECOVERY', requests.__version__, requests.__file__)\n"
    ) + predictor
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    environment = worker.executor.agents.coder.environment
    designer = worker.executor.agents.coder.designer
    if stage == "resolve":
        designer.llm.request_typed = AsyncMock(
            return_value=ExperimentDesign(
                dependencies=["PyYAML==99999"], rationale="controlled invalid dependency version"
            )
        )
        preparation = designer.environment
    else:
        preparation = environment.preparation
    preparation.repairer.diagnose = AsyncMock(
        return_value=EnvironmentRepairProposal(
            action="dependencies",
            reason="repair actual resolver/version or incomplete closure failure",
            requirements=["PyYAML==6.0.3"] if stage == "resolve" else ["urllib3>=2,<3"],
        )
    )
    summaries = []

    async def summary(messages, contract):
        assert contract is ExecutionSummary
        context = json.loads(messages[-1]["content"])
        summaries.append(context)
        assert "ACTUAL_INITIAL_RECOVERY" in context["artifacts"]["research_report"]
        history = json.loads(context["artifacts"]["environment_preparation"])
        assert any(item["attempts"][0]["error"] for item in history)
        return ExecutionSummary(response="准备失败后已修复，实际全量评测完成；仅为功能验收数据。")

    worker.executor.agents.agents["chat_agent"].llm.request_typed = summary
    plan = benchmark_plan(
        settings,
        repository,
        code=code,
        repairs=1,
        ablations=False,
        data=DATA,
        auto_design=stage == "resolve",
        environment=None
        if stage == "resolve"
        else EnvironmentSpec(requirements=["requests==2.32.5"]),
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        key = (
            "dependency_resolution_report"
            if stage == "resolve"
            else "environment_preparation_report"
        )
        preparation_report = json.loads(files.read(done.artifacts[key]))
        assert preparation_report["success"] and len(preparation_report["attempts"]) == 2
        assert preparation_report["attempts"][0]["error"]
        assert preparation_report["attempts"][0]["phase"] == stage
        assert preparation_report["decisions"][0]["accepted"]
        if stage == "resolve":
            assert preparation_report["attempts"][0]["exit_code"] != 0
            assert "99999" in preparation_report["attempts"][0]["diagnostic"]
            assert summaries and "chat_answer" in done.artifacts
        report = json.loads(files.read(done.artifacts["research_report"]))
        assert report["runs"][0]["metrics"]["accuracy"] == 1.0
        assert "ACTUAL_INITIAL_RECOVERY" in report["runs"][0]["attempts"][-1]["stdout"]
        assert "ACTUAL_INPUT_COUNT 12" in report["runs"][0]["attempts"][-1]["stdout"]
        assert len(report["environment_preparation"]) == (2 if stage == "resolve" else 1)
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()
