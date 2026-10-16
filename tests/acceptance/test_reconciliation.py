import time
from contextlib import closing, suppress

import docker
from test_docker_smoke import pytestmark as pytestmark

from openresearch.models.runtime import new_id
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker


async def test_orphan_label_recovery_is_limited_to_runtime_namespace(
    settings, repository, files
) -> None:
    sandbox = DockerSandbox(settings)
    worker = Worker(settings, repository, files, sandbox)
    labels = {
        "openresearch.managed": "true",
        "openresearch.plan_id": new_id(),
        "openresearch.execution_id": new_id(),
        "openresearch.created_at": str(time.time() - 3600),
    }
    with closing(docker.from_env()) as client:
        orphan = client.containers.run(
            settings.sandbox_image,
            ["sleep", "infinity"],
            detach=True,
            labels=labels | {"openresearch.namespace": sandbox.namespace},
        )
        other = client.containers.run(
            settings.sandbox_image,
            ["sleep", "infinity"],
            detach=True,
            labels=labels | {"openresearch.namespace": "unrelated-test-runtime"},
        )
        try:
            await worker._reconcile_sandboxes()
            assert not client.containers.list(all=True, filters={"id": orphan.id})
            assert client.containers.get(other.id).status == "running"
        finally:
            for container in (orphan, other):
                with suppress(docker.errors.NotFound):
                    container.remove(force=True)
