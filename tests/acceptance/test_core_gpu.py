import asyncio
import base64
import json
import os
import threading
from contextlib import closing
from pathlib import Path

import docker
import pytest
from test_core_checkpoints import owned_resources
from test_docker_smoke import pytestmark as docker_marks

from openresearch.checkpoint.manager import CheckpointManager
from openresearch.errors import ResourceBusyError, SandboxError
from openresearch.executor.engine import Engine
from openresearch.executor.gates import Gatekeeper, ResourceConfig
from openresearch.executor.models import DAG, ExecutorConfig, Node
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.sandbox.core.engine import CoreEngine
from openresearch.sandbox.core.models import CoreConfig
from openresearch.sandbox.core.session import CoreSandbox
from openresearch.sandbox.docker import DockerSandbox

pytestmark = [
    *docker_marks,
    pytest.mark.gpu,
    pytest.mark.skipif(
        os.getenv("OPENRESEARCH_TEST_GPU") != "1", reason="real GPU opt-in required"
    ),
]
GPU_IMAGE = "python:3.12-slim-bookworm"


@pytest.fixture(scope="module", autouse=True)
def available_gpu_image():
    with closing(docker.from_env(timeout=30)) as client:
        try:
            client.containers.get("openresearch-gpu-slot")
        except docker.errors.NotFound:
            pass
        else:
            pytest.skip("GPU slot is already held; do not remove another workload")
        try:
            client.images.get(GPU_IMAGE)
        except docker.errors.ImageNotFound:
            client.images.pull(GPU_IMAGE)


def gpu_config(path: Path, **overrides) -> CoreConfig:
    return CoreConfig(data_dir=path, image=GPU_IMAGE, gpu_enabled=True, **overrides)


def cuda_command() -> str:
    # 仅读取夹具字节；实际 CUDA 驱动、分配、kernel launch 和断言全部在容器中执行。
    code = (Path(__file__).parents[1] / "fixtures/cuda_vector_add.py").read_bytes()
    encoded = base64.b64encode(code).decode()
    return (
        "CUDA_CACHE_DISABLE=1 python -c 'import base64; "
        f'exec(compile(base64.b64decode("{encoded}"),"cuda_probe.py","exec"))'
        "'"
    )


def slot_id() -> str | None:
    with closing(docker.from_env(timeout=10)) as client:
        try:
            return str(client.containers.get("openresearch-gpu-slot").id)
        except docker.errors.NotFound:
            return None


def native_gpu(settings):
    settings.runtime_profiles = {
        "cuda12": RuntimeProfile(image=GPU_IMAGE, python="3.12", gpu=True, memory_mb=512)
    }
    settings.gpu_wait_seconds = 0
    return DockerSandbox(settings)


async def assert_cuda(sandbox):
    result = await sandbox.execute(cuda_command())
    assert result.succeeded, result.summary
    actual = json.loads(result.output)
    assert actual["values"] == [11.0, 22.0, 33.0, 44.0] and actual["device"]
    return result


async def test_actual_gpu_kernel_rollback_and_pinned_fork_keep_device_and_state(tmp_path):
    box = CoreSandbox(gpu_config(tmp_path))
    pin = None
    try:
        async with box:
            reservation = slot_id()
            assert reservation and reservation != box.container_id
            with closing(docker.from_env(timeout=10)) as client:
                actual = client.containers.get(box.container_id).attrs["HostConfig"]
                assert actual["DeviceRequests"][0]["Count"] == -1
                assert actual["DeviceRequests"][0]["Capabilities"] == [["gpu"]]
            await assert_cuda(box)
            assert (
                await box.execute("export CORE_GPU=original; printf original > state")
            ).succeeded
            manager = CheckpointManager(box)
            snapshot = await manager.commit("safe")
            assert (await box.execute("export CORE_GPU=changed; printf changed > state")).succeeded
            old_id = box.container_id
            await manager.rollback(snapshot.image_id)
            assert box.container_id != old_id and slot_id() == reservation
            assert (
                await box.execute('test "$CORE_GPU" = original && test "$(cat state)" = original')
            ).succeeded
            await assert_cuda(box)
            pin = await manager.pin("safe")
        assert slot_id() is None
        async with pin.fork() as child:
            await assert_cuda(child)
        assert slot_id() is None
    finally:
        if pin is not None:
            await pin.close()
        await box.close()
    assert owned_resources(box.namespace) == ([], [])


async def test_actual_native_gpu_owner_blocks_core_timeout_and_cancel_without_being_removed(
    tmp_path, settings
):
    native = native_gpu(settings)
    identifier = await native.create("core-admission", "native-owner", "cuda12")
    timed = CoreSandbox(gpu_config(tmp_path / "timeout", gpu_wait_seconds=0.05))
    waiting = CoreSandbox(gpu_config(tmp_path / "cancel", gpu_wait_seconds=10))
    try:
        with pytest.raises(ResourceBusyError):
            await timed.__aenter__()
        task = asyncio.create_task(waiting.__aenter__())
        try:
            await asyncio.sleep(0.3)
            assert not task.done() and slot_id() == identifier
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert slot_id() == identifier
        assert owned_resources(timed.namespace) == owned_resources(waiting.namespace) == ([], [])
    finally:
        await timed.close()
        await waiting.close()
        await native.close(identifier)
        await native.shutdown()
    assert slot_id() is None


async def test_actual_core_gpu_cleanup_failure_does_not_release_global_slot_early(
    tmp_path, settings, monkeypatch
):
    from docker.models.containers import Container

    box = CoreSandbox(gpu_config(tmp_path))
    native = native_gpu(settings)
    original = Container.remove
    failures = 0
    async with box:
        reservation, workload = slot_id(), box.container_id

        def fail_once(container, **kwargs):
            nonlocal failures
            if container.id == workload and failures == 0:
                failures += 1
                raise docker.errors.APIError("injected workload removal failure")
            return original(container, **kwargs)

        monkeypatch.setattr(Container, "remove", fail_once)
        with pytest.raises(SandboxError, match="cleanup"):
            await box.close()
        assert slot_id() == reservation
        with pytest.raises(ResourceBusyError):
            await native.create("core-admission", "must-wait", "cuda12")
        await box.close()
    assert failures == 1 and slot_id() is None
    assert owned_resources(box.namespace) == ([], [])


async def test_actual_gpu_executor_reserves_effective_weight_for_seed_and_candidates(
    tmp_path, monkeypatch
):
    from openresearch.sandbox.core.gpu import GPUReservation

    gates = Gatekeeper(ResourceConfig(max_concurrent_io=2, max_gpu_tasks=1))
    original = GPUReservation.acquire
    observations = []

    def observe(reservation, image_id):
        observations.append(gates.gpu_usage.in_use)
        assert gates.gpu_usage.in_use == 1
        return original(reservation, image_id)

    monkeypatch.setattr(GPUReservation, "acquire", observe)
    graph = DAG(
        nodes=[
            Node(
                id="cuda",
                commands=[
                    cuda_command() + " > gpu.json",
                    cuda_command() + " > gpu.json # alternative",
                ],
            ),
            Node(
                id="verify",
                commands=[
                    "python -c 'import json; "
                    'assert json.load(open("gpu.json"))["values"] == [11,22,33,44]\''
                ],
                dependencies=["cuda"],
            ),
        ]
    )
    async with Engine(ExecutorConfig(sandbox=gpu_config(tmp_path)), gates=gates) as engine:
        receipt = await engine.execute_graph(graph)
        assert receipt.status == "completed" and receipt.version == 2
        assert all(node.gpu_weight == 1 for node in receipt.nodes)
        assert all(node.gpu_weight == 0 for node in graph.nodes)
        assert all(value == 1 for value in observations) and len(observations) >= 3
        assert gates.gpu_usage.in_use == gates.gpu_usage.waiting == gates.io_usage.in_use == 0
        assert slot_id() is None
    assert slot_id() is None


async def test_actual_gpu_creation_cancel_drains_workload_before_releasing_reservation(
    tmp_path, monkeypatch
):
    started, release = threading.Event(), threading.Event()
    original = CoreEngine.start

    def delayed(engine):
        original(engine)
        started.set()
        assert release.wait(10)

    monkeypatch.setattr(CoreEngine, "start", delayed)
    box = CoreSandbox(gpu_config(tmp_path))
    task = asyncio.create_task(box.__aenter__())
    try:
        assert await asyncio.to_thread(started.wait, 20)
        assert slot_id() is not None
        task.cancel()
    finally:
        release.set()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await box.close()
    assert slot_id() is None and owned_resources(box.namespace) == ([], [])


async def test_actual_atomic_gpu_reservation_allows_only_one_core_session(tmp_path):
    boxes = [
        CoreSandbox(gpu_config(tmp_path / name, gpu_wait_seconds=0)) for name in ("one", "two")
    ]
    try:
        outcomes = await asyncio.gather(
            *(box.__aenter__() for box in boxes), return_exceptions=True
        )
        winners = [value for value in outcomes if isinstance(value, CoreSandbox)]
        assert (
            len(winners) == 1
            and sum(isinstance(value, ResourceBusyError) for value in outcomes) == 1
        )
        await assert_cuda(winners[0])
    finally:
        for box in boxes:
            await box.close()
    assert slot_id() is None and all(owned_resources(box.namespace) == ([], []) for box in boxes)


async def test_actual_gpu_waiter_acquires_device_after_native_owner_releases(
    tmp_path, settings, monkeypatch
):
    from openresearch.sandbox.core.gpu import GPUReservation

    native = native_gpu(settings)
    identifier = await native.create("gpu-admission", "release-owner", "cuda12")
    box = CoreSandbox(gpu_config(tmp_path, gpu_wait_seconds=15))
    blocked = threading.Event()
    original = GPUReservation.acquire

    def observe(reservation, image):
        try:
            return original(reservation, image)
        except ResourceBusyError:
            blocked.set()
            raise

    monkeypatch.setattr(GPUReservation, "acquire", observe)
    task = asyncio.create_task(box.__aenter__())
    try:
        assert await asyncio.to_thread(blocked.wait, 10)
        await native.close(identifier)
        await task
        await assert_cuda(box)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await box.close()
        await native.close(identifier)
        await native.shutdown()
    assert slot_id() is None and owned_resources(box.namespace) == ([], [])


async def test_actual_failed_device_probe_stops_without_cpu_fallback(tmp_path, monkeypatch):
    import openresearch.sandbox.core.engine as core_engine

    attempts = []
    original = CoreEngine.spawn

    def observe(engine, image, restore=False):
        attempts.append(engine.config.gpu_enabled)
        return original(engine, image, restore)

    # 错误注入只改本测试的探测命令；真实 GPU 容器仍创建，/bin/false 实际退出非零。
    monkeypatch.setattr(core_engine, "GPU_PROBE", "/bin/false")
    monkeypatch.setattr(CoreEngine, "spawn", observe)
    box = CoreSandbox(gpu_config(tmp_path))
    with pytest.raises(SandboxError, match="GPU device probe failed"):
        await box.__aenter__()
    await box.close()
    assert attempts == [True] and slot_id() is None and owned_resources(box.namespace) == ([], [])


async def test_actual_cpu_fork_of_gpu_snapshot_has_no_device_request_or_cuda_access(tmp_path):
    config = gpu_config(tmp_path)
    box = CoreSandbox(config)
    async with box:
        await assert_cuda(box)
        pin = await CheckpointManager(box).commit_and_pin("gpu-source")
    try:
        async with pin.fork(config.model_copy(update={"gpu_enabled": False})) as cpu:
            assert slot_id() is None and cpu.gpu_reservation_id is None
            with closing(docker.from_env(timeout=10)) as client:
                assert not client.containers.get(cpu.container_id).attrs["HostConfig"][
                    "DeviceRequests"
                ]
            actual = await cpu.execute(cuda_command())
            assert not actual.succeeded
    finally:
        await pin.close()
    assert owned_resources(box.namespace) == ([], [])
