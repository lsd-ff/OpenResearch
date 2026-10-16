import asyncio

from conftest import FakeSandbox

from openresearch.config import Settings
from openresearch.models.runtime import ExecutionResult, Status
from openresearch.planner.code import build_code_plan
from openresearch.sandbox.base import LogCallback
from openresearch.scheduler.worker import Worker
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


async def drive_worker(worker: Worker, repository: Repository, plan_id: str) -> None:
    async with asyncio.timeout(5):
        while repository.get_plan(plan_id).status not in {
            Status.COMPLETED,
            Status.FAILED,
            Status.CANCELED,
        }:
            await worker.tick()
            await asyncio.sleep(0.01)
        await asyncio.gather(*(active.future for active in worker.active.values()))


async def test_worker_produces_files_events_and_closes_session(
    settings: Settings, repository: Repository, files: ArtifactStore, sandbox: FakeSandbox
) -> None:
    plan = build_code_plan("smoke", "alice", "session", settings)
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "alice")
    worker = Worker(settings, repository, files, sandbox)
    await drive_worker(worker, repository, plan.id)
    finished = repository.get_plan(plan.id)
    assert finished.status == Status.COMPLETED
    assert set(finished.artifacts) == {"generated_code", "run_metrics"}
    assert "test-output" in files.read(finished.artifacts["run_metrics"])
    assert sandbox.created == sandbox.closed
    assert repository.cleanup_candidates("test") == []
    assert repository.list_events(plan.id)[-1].event_type == "plan_completed"


class BlockingSandbox(FakeSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def execute(self, sandbox_id: str, code: str, on_log: LogCallback) -> ExecutionResult:
        self.started.set()
        await asyncio.Future[None]()
        raise AssertionError("unreachable")


async def test_cancellation_stops_inflight_sandbox(
    settings: Settings, repository: Repository, files: ArtifactStore
) -> None:
    sandbox = BlockingSandbox()
    plan = build_code_plan("smoke", "alice", "session", settings)
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "alice")
    worker = Worker(settings, repository, files, sandbox)
    # 容器挂载源码和并发 CI 负载可能延迟启动，取消后的清理仍独立限时五秒。
    async with asyncio.timeout(15):
        while not sandbox.started.is_set():
            await worker.tick()
            await asyncio.sleep(0.01)
    repository.cancel(plan.id, "alice")
    async with asyncio.timeout(5):
        await worker.tick()
        await asyncio.gather(
            *(active.future for active in worker.active.values()), return_exceptions=True
        )
    assert sandbox.created == sandbox.closed
    assert repository.get_plan(plan.id).status == Status.CANCELED


async def test_timeout_is_failure_and_container_is_closed(
    settings: Settings, repository: Repository, files: ArtifactStore
) -> None:
    sandbox = BlockingSandbox()
    plan = build_code_plan("smoke", "alice", "session", settings)
    plan.nodes[1].timeout_seconds = 1
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "alice")
    worker = Worker(settings, repository, files, sandbox)
    await drive_worker(worker, repository, plan.id)
    assert repository.get_plan(plan.id).status == Status.FAILED
    assert "timed out" in repository.get_plan(plan.id).nodes[1].error
    assert sandbox.created == sandbox.closed
