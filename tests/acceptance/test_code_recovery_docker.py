import asyncio
import json
import os

import pytest
from test_docker_smoke import pytestmark as pytestmark

from openresearch.llm.client import LLMClient
from openresearch.models.recovery import RecoveryProposal
from openresearch.models.runtime import Status
from openresearch.planner.code import build_code_plan
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker

BROKEN_API = (
    "import json\nvalue = json.decode('{\"number\":7}')\n"
    "assert value['number'] == 7\nprint(value['number'])\n"
)


async def run_code(settings, repository, files, code, intent="运行真实代码并保留原断言"):
    plan = build_code_plan(intent, "code-repair", "docker", settings, code)
    repository.save_plan(plan)
    repository.queue_plan(plan.id, plan.owner_id)
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    try:
        async with asyncio.timeout(600):
            while True:
                await worker.tick()
                done = repository.get_plan(plan.id)
                if done.status in {Status.COMPLETED, Status.FAILED}:
                    break
                await asyncio.sleep(0.05)
        await asyncio.gather(*(active.future for active in worker.active.values()))
        assert not repository.cleanup_candidates("docker")
        return done, json.loads(files.read(done.artifacts["code_recovery_report"]))
    finally:
        await worker._shutdown()


async def test_real_api_failure_then_new_container_executes_actual_repaired_code(
    settings, repository, files, monkeypatch
):
    # 仅模型提议为夹具；异常、两个容器、实际数值和输出均来自真实执行。
    async def propose(self, messages, contract):
        assert contract is RecoveryProposal
        assert "AttributeError" in messages[-1]["content"]
        return RecoveryProposal(
            action="code",
            reason="Use the actual json.loads API.",
            code=BROKEN_API.replace("json.decode", "json.loads"),
        )

    monkeypatch.setattr(LLMClient, "request_typed", propose)
    done, report = await run_code(settings, repository, files, BROKEN_API)
    assert done.status == Status.COMPLETED
    assert [item["output"]["exit_code"] for item in report["attempts"]] == [1, 0]
    assert len({item["sandbox_id"] for item in report["attempts"]}) == 2
    assert report["attempts"][-1]["output"]["stdout"].strip() == "7"
    assert all(item["container_image_id"].startswith("sha256:") for item in report["attempts"])


async def test_real_independent_assertion_failure_is_preserved(
    settings, repository, files, monkeypatch
):
    async def forbidden(*args, **kwargs):
        raise AssertionError("a failing assertion must not invoke a model to weaken the test")

    monkeypatch.setattr(LLMClient, "request_typed", forbidden)
    done, report = await run_code(settings, repository, files, "assert 3 * 3 == 8\n")
    assert done.status == Status.FAILED
    assert len(report["attempts"]) == 1 and not report["decisions"]
    assert "AssertionError" in report["attempts"][0]["output"]["stderr"]


@pytest.mark.external
@pytest.mark.skipif(
    os.environ.get("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="PyPI opt-in required"
)
async def test_real_missing_yaml_gets_pinned_verified_wheel_and_reexecutes(
    settings, repository, files
):
    code = (
        "import yaml\nvalue = yaml.safe_load('number: 9')\n"
        "assert value['number'] == 9\nprint(value['number'])\n"
    )
    done, report = await run_code(
        settings, repository, files, code, "使用 PyYAML==6.0.3 运行代码，保持所有断言"
    )
    assert done.status == Status.COMPLETED, [task.error for task in done.nodes]
    assert [item["output"]["exit_code"] for item in report["attempts"]] == [1, 0]
    assert "ModuleNotFoundError" in report["attempts"][0]["output"]["stderr"]
    assert "pyyaml" in report["attempts"][-1]["environment"]["installed"]
    assert report["attempts"][-1]["environment"]["installed"]["pyyaml"] == "6.0.3"
    assert report["attempts"][-1]["environment_lock"]["wheels"]
    assert report["attempts"][-1]["output"]["stdout"].strip() == "9"
