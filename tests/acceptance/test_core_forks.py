import asyncio
import threading
from pathlib import Path

import docker
import pytest
from test_core_checkpoints import available_core_image as available_core_image
from test_core_checkpoints import owned_resources
from test_docker_smoke import pytestmark as pytestmark

from openresearch.checkpoint.manager import CheckpointManager
from openresearch.checkpoint.registry import Snapshot
from openresearch.errors import SandboxError
from openresearch.sandbox.core.engine import CoreEngine
from openresearch.sandbox.core.models import CoreConfig
from openresearch.sandbox.core.session import CoreSandbox


async def test_actual_pinned_snapshot_survives_parent_and_forks_isolated_shells(tmp_path: Path):
    config = CoreConfig(
        data_dir=tmp_path, image="python:3.12-slim", environment={"REMOVED": "original"}
    )
    parent = CoreSandbox(config)
    async with parent:
        assert (
            await parent.execute(
                "unset REMOVED; export VALUE='中文'; mkdir data; cd data; "
                "printf original > value; get_value() { printf '%s:' \"$VALUE\"; cat value; }"
            )
        ).succeeded
        pin = await CheckpointManager(parent).commit_and_pin("fork-base")
    try:
        assert owned_resources(parent.namespace) == ([], [pin.snapshot.image_id])
        async with pin.fork() as left, pin.fork() as right:
            assert left.container_id != right.container_id
            one, two = await asyncio.gather(
                left.execute("printf left > value; sleep 0.1; get_value"),
                right.execute('test -z "${REMOVED+x}" && get_value'),
            )
            assert one.succeeded and one.output == "中文:left"
            assert two.succeeded and two.output == "中文:original"
            assert (await left.execute('test "$PWD" = /workspace/data')).succeeded
        assert owned_resources(parent.namespace) == ([], [pin.snapshot.image_id])
    finally:
        await pin.close()
    await pin.close()
    assert owned_resources(parent.namespace) == ([], [])


async def test_actual_descendant_pin_keeps_ancestors_until_last_reference_closes(tmp_path: Path):
    parent = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with parent:
        assert (await parent.execute("printf root > root-file")).succeeded
        first = await CheckpointManager(parent).commit_and_pin("root")
    try:
        async with first.fork() as child:
            # 活容器已接过镜像引用；显式租约关闭不能提前删除它使用的父镜像。
            await first.close()
            assert (await child.execute("printf child > child-file")).succeeded
            second = await CheckpointManager(child).commit_and_pin("child")
        try:
            containers, images = owned_resources(parent.namespace)
            assert not containers and len(images) == 2
            async with second.fork() as grandchild:
                actual = await grandchild.execute("cat root-file child-file")
                assert actual.succeeded and actual.output == "rootchild"
        finally:
            await second.close()
    finally:
        await first.close()
    assert owned_resources(parent.namespace) == ([], [])


async def test_actual_closed_pin_cannot_start_new_fork(tmp_path: Path):
    parent = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with parent:
        pin = await CheckpointManager(parent).commit_and_pin("closed")
    pending = pin.fork()
    await pin.close()
    with pytest.raises(SandboxError, match="closed"):
        async with pending:
            pytest.fail("a closed lease cannot grant a new image reference")
    await pending.close()
    assert owned_resources(parent.namespace) == ([], [])


async def test_actual_pin_existing_snapshot_checks_session_and_rejects_raw_image_start(
    tmp_path: Path,
):
    from contextlib import closing

    parent = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with parent:
        manager = CheckpointManager(parent)
        snapshot = await manager.commit("existing")
        pin = await manager.pin(snapshot.node_id)
        with closing(docker.from_env(timeout=10)) as client:
            base_id = str(client.images.get("python:3.12-slim").id)
        manager.registry.register(Snapshot("foreign", base_id, 1))
        with pytest.raises(SandboxError, match="does not belong"):
            await manager.pin("foreign")
        with pytest.raises(KeyError):
            await manager.pin("unknown")
    try:
        raw = CoreSandbox(parent.config.model_copy(update={"image": snapshot.image_id}))
        with pytest.raises(SandboxError, match="live snapshot lease"):
            async with raw:
                pytest.fail("a string image ID does not grant managed snapshot ownership")
        async with pin.fork() as child:
            assert (await child.execute("true")).succeeded
    finally:
        await pin.close()
    assert owned_resources(parent.namespace) == ([], [])


async def test_actual_fork_start_cancellation_drains_and_releases_borrowed_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    parent = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with parent:
        pin = await CheckpointManager(parent).commit_and_pin("cancel-start")
    started, release = threading.Event(), threading.Event()
    original = CoreEngine.start

    def delayed_start(engine: CoreEngine):
        original(engine)
        started.set()
        assert release.wait(10)

    monkeypatch.setattr(CoreEngine, "start", delayed_start)
    child = pin.fork()
    try:
        task = asyncio.create_task(child.__aenter__())
        try:
            assert await asyncio.to_thread(started.wait, 15)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owned_resources(parent.namespace) == ([], [pin.snapshot.image_id])
    finally:
        release.set()
        await child.close()
        await pin.close()
    assert owned_resources(parent.namespace) == ([], [])


async def test_actual_pin_cancellation_releases_unreturned_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    created, release = threading.Event(), threading.Event()
    original = CoreEngine.pin

    def delayed_pin(engine: CoreEngine, snapshot):
        pin = original(engine, snapshot)
        created.set()
        assert release.wait(10)
        return pin

    monkeypatch.setattr(CoreEngine, "pin", delayed_pin)
    parent = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with parent:
        manager = CheckpointManager(parent)
        task = asyncio.create_task(manager.commit_and_pin("canceled-observer"))
        try:
            assert await asyncio.to_thread(created.wait, 15)
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager.registry.latest().node_id == "canceled-observer"
    assert owned_resources(parent.namespace) == ([], [])


async def test_actual_failed_fork_does_not_leak_image_reference(tmp_path: Path):
    parent = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with parent:
        pin = await CheckpointManager(parent).commit_and_pin("failed-start")
    try:
        config = parent.config.model_copy(update={"runtime": "openresearch-missing-runtime"})
        with pytest.raises(docker.errors.DockerException):
            async with pin.fork(config):
                pytest.fail("missing runtime cannot start a fork")
        assert owned_resources(parent.namespace) == ([], [pin.snapshot.image_id])
    finally:
        await pin.close()
    assert owned_resources(parent.namespace) == ([], [])


async def test_actual_image_delete_failure_can_retry_without_double_releasing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from docker.models.images import ImageCollection

    parent = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with parent:
        pin = await CheckpointManager(parent).commit_and_pin("cleanup-retry")
    original = ImageCollection.remove
    failures = 0

    def fail_once(collection, image, **kwargs):
        nonlocal failures
        if image == pin.snapshot.image_id and failures == 0:
            failures += 1
            raise docker.errors.APIError("injected temporary image removal failure")
        return original(collection, image, **kwargs)

    monkeypatch.setattr(ImageCollection, "remove", fail_once)
    try:
        with pytest.raises(SandboxError, match="cleanup"):
            await pin.close()
        assert owned_resources(parent.namespace) == ([], [pin.snapshot.image_id])
    finally:
        await pin.close()
    assert failures == 1 and owned_resources(parent.namespace) == ([], [])
