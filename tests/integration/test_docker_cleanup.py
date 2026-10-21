import asyncio
import threading
from unittest.mock import Mock

import docker
import pytest
from requests.exceptions import ReadTimeout

from openresearch.errors import SandboxError
from openresearch.sandbox.docker import DockerSandbox


@pytest.mark.parametrize("deleted", [False, True])
async def test_transport_failure_retries_the_owned_id_and_finishes_workspace_cleanup(
    settings, monkeypatch, deleted
):
    sandbox = DockerSandbox(settings)
    container, volume, client = Mock(), Mock(), Mock()
    container.labels = {
        "openresearch.managed": "true",
        "openresearch.namespace": sandbox.namespace,
        "openresearch.workspace_volume": "owned-volume",
    }
    volume.attrs = {
        "Labels": {"openresearch.workspace": "true", "openresearch.namespace": sandbox.namespace}
    }
    container.remove.side_effect = [
        ReadTimeout("ambiguous Docker response"),
        docker.errors.NotFound("already removed") if deleted else None,
    ]
    client.containers.get.return_value, client.volumes.get.return_value = container, volume
    monkeypatch.setattr(docker, "from_env", lambda **kwargs: client)
    await sandbox.close("immutable-id")
    assert container.remove.call_count == 2
    client.containers.get.assert_called_once_with("immutable-id")
    volume.remove.assert_called_once()


@pytest.mark.parametrize("failure", ["persistent", "permission", "foreign"])
async def test_cleanup_does_not_hide_persistent_errors_or_remove_foreign_resources(
    settings, monkeypatch, failure
):
    sandbox = DockerSandbox(settings)
    container, client = Mock(), Mock()
    container.labels = {
        "openresearch.managed": "true",
        "openresearch.namespace": "foreign" if failure == "foreign" else sandbox.namespace,
    }
    container.remove.side_effect = (
        docker.errors.APIError("permission denied")
        if failure == "permission"
        else ReadTimeout("still unavailable")
    )
    client.containers.get.return_value = container
    monkeypatch.setattr(docker, "from_env", lambda **kwargs: client)
    with pytest.raises((ReadTimeout, docker.errors.APIError, SandboxError)):
        await sandbox.close("immutable-id")
    assert container.remove.call_count == {"persistent": 2, "permission": 1, "foreign": 0}[failure]
    client.volumes.get.assert_not_called()


async def test_cancel_waits_for_the_already_running_delete_transaction(settings, monkeypatch):
    sandbox = DockerSandbox(settings)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def cleanup(identifier):
        entered.set()
        assert release.wait(5)
        finished.set()

    monkeypatch.setattr(sandbox, "_close", cleanup)
    task = asyncio.create_task(sandbox.close("owned-id"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set()
