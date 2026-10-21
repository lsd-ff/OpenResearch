import asyncio
import threading
from contextlib import closing
from uuid import uuid4

import docker
import pytest
from test_core_checkpoints import available_core_image as available_core_image
from test_core_checkpoints import owned_resources
from test_docker_smoke import pytestmark as pytestmark

from openresearch.errors import ResourceBusyError
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.sandbox.core.cleanup import remove_owned_containers
from openresearch.sandbox.core.engine import CoreEngine
from openresearch.sandbox.core.gpu import GPUReservation
from openresearch.sandbox.core.models import CoreConfig
from openresearch.sandbox.core.session import CoreSandbox
from openresearch.sandbox.docker import DockerSandbox


def reservation_labels():
    return {
        "openresearch.core.managed": "true",
        "openresearch.core.namespace": uuid4().hex,
        "openresearch.core.session": uuid4().hex,
    }


def require_free_slot(client):
    try:
        client.containers.get("openresearch-gpu-slot")
    except docker.errors.NotFound:
        return
    pytest.skip("GPU slot already held; do not touch a foreign workload")


async def test_actual_cpu_control_reservation_is_atomic_and_blocks_native_adapter(settings):
    # 实际 CPU 控制容器证明占用，不把它当作 GPU 可用/计算成功的证据。
    with closing(docker.from_env(timeout=30)) as client:
        require_free_slot(client)
        reservations = [GPUReservation(client, reservation_labels()) for _ in range(2)]
        image_id = str(client.images.get("python:3.12-slim").id)
        try:
            results = await asyncio.gather(
                *(asyncio.to_thread(item.acquire, image_id) for item in reservations),
                return_exceptions=True,
            )
            assert sum(value is None for value in results) == 1
            assert sum(isinstance(value, ResourceBusyError) for value in results) == 1
            settings.runtime_profiles = {
                "gpu": RuntimeProfile(image="python:3.12-slim", python="3.12", gpu=True)
            }
            settings.gpu_wait_seconds = 0
            native = DockerSandbox(settings)
            with pytest.raises(ResourceBusyError):
                await native.create("admission", "must-wait", "gpu")
            await native.shutdown()
        finally:
            for item in reservations:
                remove_owned_containers(client, item.labels)
        assert all(
            owned_resources(item.labels["openresearch.core.namespace"]) == ([], [])
            for item in reservations
        )


async def test_actual_busy_core_admission_cancel_leaves_the_foreign_reservation(
    tmp_path, monkeypatch
):
    with closing(docker.from_env(timeout=30)) as client:
        require_free_slot(client)
        holder = GPUReservation(client, reservation_labels())
        holder.acquire(str(client.images.get("python:3.12-slim").id))
        blocked = threading.Event()
        original = GPUReservation.acquire

        def observe(reservation, image):
            try:
                return original(reservation, image)
            except ResourceBusyError:
                blocked.set()
                raise

        monkeypatch.setattr(GPUReservation, "acquire", observe)
        box = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim", gpu_enabled=True))
        task = asyncio.create_task(box.__aenter__())
        try:
            assert await asyncio.to_thread(blocked.wait, 10)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert client.containers.get("openresearch-gpu-slot").id == holder.id
            assert owned_resources(box.namespace) == ([], [])
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await box.close()
            remove_owned_containers(client, holder.labels)


async def test_actual_invalid_gpu_runtime_is_not_retried_as_cpu(tmp_path, monkeypatch):
    with closing(docker.from_env(timeout=30)) as client:
        require_free_slot(client)
    attempts = []
    original = CoreEngine.spawn

    def observe(engine, image, restore=False):
        attempts.append(engine.config.gpu_enabled)
        return original(engine, image, restore)

    monkeypatch.setattr(CoreEngine, "spawn", observe)
    box = CoreSandbox(
        CoreConfig(
            data_dir=tmp_path,
            image="python:3.12-slim",
            gpu_enabled=True,
            runtime="openresearch-invalid-runtime",
            gpu_wait_seconds=0,
        )
    )
    with pytest.raises(docker.errors.DockerException):
        await box.__aenter__()
    await box.close()
    assert attempts == [True] and owned_resources(box.namespace) == ([], [])
