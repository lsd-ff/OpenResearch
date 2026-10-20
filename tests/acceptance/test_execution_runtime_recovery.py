import json
import os
from unittest.mock import AsyncMock

import pytest
from test_benchmark_preflight import COUNTED, DATA
from test_docker_smoke import pytestmark as pytestmark
from test_research import PREDICTOR, benchmark_plan, finish

from openresearch.models.environment_repair import EnvironmentRepairProposal
from openresearch.models.runtime import Status
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker


@pytest.mark.external
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="PyPI opt-in required")
@pytest.mark.parametrize("mode", ["preflight", "full", "budget", "recheck_failed", "third_repair"])
async def test_runtime_change_keeps_code_and_requires_matching_preflight_with_shared_budget(
    settings, repository, files, mode
):
    settings.runtime_profiles = {
        "cpu13": RuntimeProfile(image="python:3.13-slim-bookworm", python="3.13", memory_mb=512)
    }
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    recovery = worker.executor.agents.coder.research_executor.repairs.environment_repairer
    recovery.diagnose = AsyncMock(
        return_value=EnvironmentRepairProposal(
            action="dependencies",
            reason="actual missing public library",
            requirements=["anyenv==1.9.7"],
        )
    )
    worker.executor.agents.coder.research_executor.preparer.propose = AsyncMock(
        side_effect=AssertionError(
            "package plus runtime change already spends the two-repair budget"
        )
    )
    dependency_code = (
        "import anyenv, importlib.metadata\n"
        "assert importlib.metadata.version('anyenv') == '1.9.7'\n"
        "assert sys.version_info[:2] == (3, 13)\n"
        "assert anyenv.load_json('{\"number\": 71}')['number'] == 71\n"
        "print('ACTUAL_EXECUTION_RUNTIME', 71, sys.version.split()[0], anyenv.__file__)\n"
    )
    if mode == "third_repair":
        dependency_code += "anyenv.this_api_does_not_exist()\n"
    code = PREDICTOR.replace("print('real predictions written')", COUNTED)
    if mode in {"preflight", "budget"}:
        code += dependency_code
    else:
        code += "if len(rows) > 8:\n" + "".join(
            "    " + line + "\n" for line in dependency_code.splitlines()
        )
    if mode == "recheck_failed":
        code += (
            "if len(rows) <= 8 and sys.version_info[:2] == (3, 13):\n"
            "    raise RuntimeError('RUNTIME_RECHECK_BLOCKED')\n"
        )
    plan = benchmark_plan(
        settings,
        repository,
        code=code,
        repairs=1 if mode == "budget" else 2,
        ablations=False,
        data=DATA,
        intent="保持anyenv==1.9.7，执行原代码和真实数据",
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id, timeout_seconds=300)
        done = repository.get_plan(plan.id)
        if mode == "budget":
            assert done.status == Status.FAILED
            assert "research_report" not in done.artifacts
            preflight = json.loads(files.read(done.artifacts["benchmark_preflight_report"]))
            assert preflight["status"] == "failed" and len(preflight["run"]["attempts"]) == 1
            assert not preflight["environment_changes"][0]["accepted"]
            assert preflight["environment_changes"][0]["preparation"]["attempts"][0]["error"]
        else:
            report = json.loads(files.read(done.artifacts["research_report"]))
            assert report["runtime"]["profile"] == "cpu13"
            assert report["final_environment_lock"]["python"] == "3.13"
            changes = (
                report["preflight"]["environment_changes"]
                if mode == "preflight"
                else report["environment_changes"]
            )
            assert len(changes) == 1 and changes[0]["repair_cost"] == 2
            assert changes[0]["runtime_after"]["profile"] == "cpu13"
            assert changes[0]["preparation"]["attempts"][0]["error"]
            if mode in {"preflight", "full"}:
                assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
                assert report["runs"][0]["metrics"]["accuracy"] == 1.0
                assert (
                    "ACTUAL_EXECUTION_RUNTIME 71 3.13."
                    in report["runs"][0]["attempts"][-1]["stdout"]
                )
                checked = (
                    report["preflight"] if mode == "preflight" else report["preflight_rechecks"][0]
                )
                assert checked["runtime"]["profile"] == "cpu13" and checked["status"] == "passed"
                assert len(checked["sample_ids"]) == 8
                assert checked["environment_lock_sha256"] == report["environment_lock_sha256"]
                assert (
                    checked["run"]["attempts"][-1]["sandbox_id"]
                    != report["runs"][0]["attempts"][-1]["sandbox_id"]
                )
            else:
                assert done.status == Status.FAILED and not report["success"]
                if mode == "recheck_failed":
                    assert report["preflight_rechecks"][0]["status"] == "failed"
                    assert len(report["runs"][0]["attempts"]) == 1
                    assert "RUNTIME_RECHECK_BLOCKED" in report["runs"][0]["attempts"][-1]["error"]
                else:
                    assert len(report["runs"][0]["attempts"]) == 2
                    assert "AttributeError" in report["runs"][0]["attempts"][-1]["stderr"]
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()
