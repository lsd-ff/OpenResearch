import os
import time

from openresearch.models.execution import ExecutionLease, TaskCompletion
from openresearch.models.runtime import TaskResult, new_id
from openresearch.planner.code import build_code_plan
from openresearch.storage.garbage import ArtifactGarbageCollector


def old_artifact(files, lease, key="generated_code"):
    artifact = files.write(lease, key, "print(15)")
    path = files.root / artifact.location
    os.utime(path, (time.time() - 7200, time.time() - 7200))
    return artifact


def test_gc_preserves_active_and_historical_artifacts(repository, files, settings) -> None:
    garbage = ArtifactGarbageCollector(repository.database, files.root)
    orphan = old_artifact(files, ExecutionLease(new_id(), new_id(), new_id(), "lost"))
    plan = build_code_plan("smoke", "alice", "session", settings)
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "alice")
    claimed, node = repository.claim_next("worker", 30, 1)
    lease = ExecutionLease.for_task(claimed, node, "worker")
    artifact = old_artifact(files, lease)
    assert garbage.collect() == 1
    assert not (files.root / orphan.location).exists()
    assert (files.root / artifact.location).exists()
    repository.finish(
        lease, TaskCompletion(TaskResult(outputs={"generated_code": "print(15)"}), [artifact])
    )
    assert garbage.collect() == 0
    assert repository.get_artifact(plan.id, artifact.id, "alice").sha256 == artifact.sha256
