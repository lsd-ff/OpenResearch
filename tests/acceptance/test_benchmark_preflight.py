import hashlib
import json

import pytest
from test_docker_smoke import pytestmark as pytestmark
from test_research import PREDICTOR, benchmark_plan, finish

from openresearch.models.research import FilePatch, ResearchPatch
from openresearch.models.runtime import Status
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker

DATA = ("id,x,y\n" + "".join(f"{index},1,yes\n" for index in range(12))).encode()
COUNTED = "print('ACTUAL_INPUT_COUNT', len(rows))\n"


async def test_eight_real_samples_precede_full_dataset_in_independent_containers(
    settings,
    repository,
    files,
    monkeypatch,
):
    code = PREDICTOR.replace("print('real predictions written')", COUNTED)
    sandbox = DockerSandbox(settings)
    create = sandbox.create
    created = []

    async def record_container(plan_id, execution_id):
        identifier = await create(plan_id, execution_id)
        created.append(identifier)
        return identifier

    monkeypatch.setattr(sandbox, "create", record_container)
    worker = Worker(settings, repository, files, sandbox)
    plan = benchmark_plan(settings, repository, code=code, ablations=False, data=DATA)
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        report = json.loads(files.read(done.artifacts["research_report"]))
        preflight = report["preflight"]
        assert preflight["sample_ids"] == [str(index) for index in range(8)]
        assert preflight["total_rows"] == 12
        assert "ACTUAL_INPUT_COUNT 8" in preflight["run"]["attempts"][-1]["stdout"]
        assert "ACTUAL_INPUT_COUNT 12" in report["runs"][0]["attempts"][-1]["stdout"]
        assert report["runs"][0]["metrics"]["accuracy"] == 1.0
        assert (
            len(json.loads(files.read(done.artifacts["benchmark_predictions"]))["baseline"]) == 12
        )
        assert preflight["patch_sha256"] == report["runs"][0]["attempts"][0]["patch_sha256"]
        actual_runs = {
            preflight["run"]["attempts"][-1]["sandbox_id"],
            report["runs"][0]["attempts"][-1]["sandbox_id"],
        }
        assert len(actual_runs) == 2 and actual_runs <= set(created)
        assert len(created) == len(set(created)) == 3  # 另一个是独立只读版本探测。
        assert repository.cleanup_candidates("docker") == []
    finally:
        await worker._shutdown()


async def test_failed_preflight_preserves_logs_and_never_runs_full_dataset(
    settings,
    repository,
    files,
):
    code = PREDICTOR + "\nraise RuntimeError('PREFLIGHT_BLOCKED_FULL_RUN')\n"
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    plan = benchmark_plan(settings, repository, code=code, ablations=False, data=DATA)
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.FAILED
        report = json.loads(files.read(done.artifacts["benchmark_preflight_report"]))
        assert report["status"] == "failed" and len(report["sample_ids"]) == 8
        assert "PREFLIGHT_BLOCKED_FULL_RUN" in report["run"]["attempts"][-1]["stderr"]
        full = next(node for node in done.nodes if node.type == "research_execute")
        assert full.run_count == 0 and "research_report" not in done.artifacts
        assert repository.cleanup_candidates("docker") == []
    finally:
        await worker._shutdown()


async def test_passed_preflight_does_not_hide_failure_on_later_full_dataset_rows(
    settings,
    repository,
    files,
):
    code = PREDICTOR + "\nif len(rows) > 8:\n    raise RuntimeError('TAIL_ROW_FAILURE')\n"
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    plan = benchmark_plan(settings, repository, code=code, ablations=False, data=DATA)
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.FAILED
        report = json.loads(files.read(done.artifacts["research_report"]))
        assert report["preflight"]["status"] == "passed"
        assert not report["success"]
        assert "TAIL_ROW_FAILURE" in report["runs"][0]["attempts"][-1]["stderr"]
        assert report["runs"][0]["metrics"] == {}
    finally:
        await worker._shutdown()


@pytest.mark.parametrize("mode", ["passed", "blocked", "repaired"])
async def test_actual_patch_recheck_blocks_full_retry_until_sample_passes(
    settings, repository, files, monkeypatch, mode
):
    predictor = PREDICTOR.replace("print('real predictions written')", COUNTED)
    initial = predictor + "\nif len(rows) > 8:\n    raise RuntimeError('TAIL_ROW_FAILURE')\n"
    sandbox = DockerSandbox(settings)
    created = []
    create = sandbox.create

    async def record_container(plan_id, execution_id):
        identifier = await create(plan_id, execution_id)
        created.append(identifier)
        return identifier

    monkeypatch.setattr(sandbox, "create", record_container)
    worker = Worker(settings, repository, files, sandbox)
    proposals = []

    async def propose(intent, spec, sources, error):
        proposals.append(error)
        code = predictor
        if mode != "passed" and len(proposals) == 1:
            code += "\nraise RuntimeError('RECHECK_BLOCKED_FULL_RETRY')\n"
        return ResearchPatch(
            files=[
                FilePatch(
                    path=spec.entrypoint,
                    base_sha256=hashlib.sha256(sources[spec.entrypoint]).hexdigest(),
                    content=code,
                )
            ]
        )

    monkeypatch.setattr(worker.executor.research_executor.preparer, "propose", propose)
    plan = benchmark_plan(
        settings,
        repository,
        code=initial,
        repairs=2 if mode == "repaired" else 1,
        ablations=False,
        data=DATA,
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == (Status.FAILED if mode == "blocked" else Status.COMPLETED), [
            node.error for node in done.nodes
        ]
        report = json.loads(files.read(done.artifacts["research_report"]))
        rechecks = report["preflight_rechecks"]
        assert [item["status"] for item in rechecks] == {
            "passed": ["passed"],
            "blocked": ["failed"],
            "repaired": ["failed", "passed"],
        }[mode]
        assert all(
            "ACTUAL_INPUT_COUNT 8" in item["run"]["attempts"][-1]["stdout"] for item in rechecks
        )
        attempts = report["runs"][0]["attempts"]
        assert "TAIL_ROW_FAILURE" in attempts[0]["stderr"]
        assert len(attempts) == (1 if mode == "blocked" else 2)
        actual_runs = {
            attempt["sandbox_id"]
            for run in [
                report["preflight"]["run"],
                *[item["run"] for item in rechecks],
                report["runs"][0],
            ]
            for attempt in run["attempts"]
        }
        expected_runs = {"passed": 4, "blocked": 3, "repaired": 5}[mode]
        assert len(actual_runs) == expected_runs and actual_runs <= set(created)
        assert len(created) == len(set(created)) == expected_runs + 1
        if mode == "blocked":
            assert not report["success"] and report["runs"][0]["metrics"] == {}
        else:
            assert "ACTUAL_INPUT_COUNT 12" in attempts[-1]["stdout"]
            assert attempts[-1]["exit_code"] == 0
            assert rechecks[-1]["patch_sha256"] == attempts[-1]["patch_sha256"]
            assert report["runs"][0]["metrics"]["accuracy"] == 1.0
        if mode == "repaired":
            assert "RECHECK_BLOCKED_FULL_RETRY" in proposals[-1]
        assert repository.cleanup_candidates("docker") == []
    finally:
        await worker._shutdown()
