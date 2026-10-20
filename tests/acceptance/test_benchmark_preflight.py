import json

from test_docker_smoke import pytestmark as pytestmark
from test_research import PREDICTOR, benchmark_plan, finish

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
        assert len(created) == 2 and len(set(created)) == 2
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
