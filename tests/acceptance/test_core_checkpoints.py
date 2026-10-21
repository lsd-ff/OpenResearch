import asyncio
import threading
from contextlib import closing
from pathlib import Path

import docker
import pytest
from test_docker_smoke import pytestmark as pytestmark

from openresearch.checkpoint.manager import CheckpointManager
from openresearch.checkpoint.registry import Snapshot
from openresearch.errors import SandboxError
from openresearch.sandbox.core.engine import CoreEngine
from openresearch.sandbox.core.models import CoreConfig
from openresearch.sandbox.core.session import CoreSandbox


@pytest.fixture(scope="module", autouse=True)
def available_core_image():
    with closing(docker.from_env(timeout=30)) as client:
        try:
            client.images.get("python:3.12-slim")
        except docker.errors.ImageNotFound:
            client.images.pull("python:3.12-slim")


def owned_resources(namespace: str) -> tuple[list[str], list[str]]:
    with closing(docker.from_env(timeout=5)) as client:
        filters = {"label": f"openresearch.core.namespace={namespace}"}
        return (
            [item.id for item in client.containers.list(all=True, filters=filters)],
            [item.id for item in client.images.list(filters=filters)],
        )


async def test_actual_checkpoint_restores_files_exported_environment_and_cwd(tmp_path: Path):
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with sandbox:
        original = sandbox.container_id
        assert (await sandbox.execute("export CORE_VALUE='第一版'; mkdir data; cd data")).succeeded
        assert (await sandbox.execute("printf preserved > value; touch keep")).succeeded
        manager = CheckpointManager(sandbox)
        first = await manager.commit("节点 A / stable")
        assert (
            await sandbox.execute("rm keep; printf changed > value; export CORE_VALUE=wrong")
        ).succeeded
        await manager.commit("later")
        assert (await sandbox.execute("cd /; touch /extra")).succeeded
        receipt = await manager.rollback_to_node(first.node_id)
        assert receipt.old_container_id == original
        assert receipt.new_container_id == sandbox.container_id != original
        result = await sandbox.execute("printf '%s:' \"$CORE_VALUE\"; cat value; test -f keep")
        assert result.succeeded and result.output == "第一版:preserved"
        assert (await sandbox.execute("test ! -e /extra")).succeeded
        assert manager.registry.all() == (first,)
        with pytest.raises(KeyError):
            await manager.rollback_to_node("later")
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_terminal_serializes_commands_and_preserves_real_exit_codes(tmp_path: Path):
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with sandbox:
        first, second = await asyncio.gather(
            sandbox.execute("sleep 0.1; export CORE_VALUE=done; printf first"),
            sandbox.execute("printf '%s' \"$CORE_VALUE\""),
        )
        assert first.output == "first" and second.output == "done"
        assert (await sandbox.execute("true")).succeeded
        failed = await sandbox.execute("printf problem >&2; (exit 7)")
        assert not failed.succeeded and failed.exit_code == 7 and failed.output == "problem"
        assert failed.audit_path.read_bytes() == b"problem"
    assert owned_resources(sandbox.namespace) == ([], [])


@pytest.mark.parametrize("interruption", ["timeout", "cancel", "output_limit"])
async def test_actual_interruption_drains_terminal_and_next_command_works(
    tmp_path: Path, interruption: str
):
    sandbox = CoreSandbox(
        CoreConfig(data_dir=tmp_path, image="python:3.12-slim", max_output_bytes=4096)
    )
    async with sandbox:
        if interruption == "output_limit":
            result = await sandbox.execute("python -c 'print(\"x\" * 1000000)'")
            assert result.output_limited and not result.succeeded
            assert len(result.audit_path.read_bytes()) == 4096
        elif interruption == "timeout":
            result = await sandbox.execute("printf started; sleep 60", timeout_seconds=0.2)
            assert result.timed_out and not result.succeeded and "started" in result.output
        else:
            task = asyncio.create_task(sandbox.execute("sleep 60"))
            await asyncio.sleep(0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        result = await sandbox.execute("printf recovered", timeout_seconds=5)
        assert result.succeeded and result.output == "recovered"
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_shell_exit_closes_owned_resources(tmp_path: Path):
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    with pytest.raises(SandboxError):
        async with sandbox:
            await sandbox.execute("exit 9")
    await sandbox.close()
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_checkpoint_keeps_functions_and_quoted_exports_without_reviving_unset_values(
    tmp_path: Path,
):
    config = CoreConfig(
        data_dir=tmp_path, image="python:3.12-slim", environment={"REMOVED": "original"}
    )
    sandbox = CoreSandbox(config)
    async with sandbox:
        assert (
            await sandbox.execute(
                "unset REMOVED; export CORE_VALUE=$'a\\x27b\\n中文'; "
                "core_function() { printf '%s' \"$CORE_VALUE\"; }; "
                "mkdir $'quoted\\x27dir'; cd $'quoted\\x27dir'"
            )
        ).succeeded
        manager = CheckpointManager(sandbox)
        snapshot = await manager.commit("exported")
        assert (await sandbox.execute("unset -f core_function; export CORE_VALUE=wrong")).succeeded
        await manager.rollback(snapshot.image_id)
        actual = await sandbox.execute('test -z "${REMOVED+x}" && core_function')
        assert actual.succeeded and actual.output == "a'b\n中文"
        assert (await sandbox.execute("test \"$PWD\" = $'/workspace/quoted\\x27dir'")).succeeded
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_terminal_accepts_long_commands_and_bounds_summary(tmp_path: Path):
    config = CoreConfig(
        data_dir=tmp_path, image="python:3.12-slim", keep_first_lines=1, keep_last_lines=1
    )
    sandbox = CoreSandbox(config)
    async with sandbox:
        result = await sandbox.execute("CORE_BIG='" + "z" * 10000 + "'; printf '%s' ${#CORE_BIG}")
        assert result.succeeded and result.output == "10000"
        result = await sandbox.execute("printf '中文一\\n中间二\\n中间三\\n中文四\\n'")
        assert result.succeeded and result.output.count("\n") == 4
        assert "中文一" in result.summary and "中文四" in result.summary
        assert "2 lines omitted" in result.summary and "中间" not in result.summary
        result = await sandbox.execute("printf 'x\\r\\ny\\n'")
        assert result.succeeded and result.audit_path.read_bytes() == b"x\r\ny\n"
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_rollback_rejects_unowned_image_without_changing_current_state(tmp_path: Path):
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with sandbox:
        original_id = sandbox.container_id
        manager = CheckpointManager(sandbox)
        with closing(docker.from_env(timeout=5)) as client:
            base_image = client.images.get("python:3.12-slim").id
        manager.registry.register(Snapshot("foreign", base_image, 1))
        with pytest.raises(SandboxError, match="does not belong"):
            await manager.rollback_to_latest()
        assert sandbox.container_id == original_id
        assert manager.registry.latest().node_id == "foreign"
        assert (await sandbox.execute("printf still-running")).output == "still-running"
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_start_cancellation_waits_for_creation_then_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    entered, release = threading.Event(), threading.Event()
    original = CoreEngine.start

    def delayed_start(engine: CoreEngine) -> None:
        entered.set()
        assert release.wait(5)
        original(engine)

    monkeypatch.setattr(CoreEngine, "start", delayed_start)
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    task = asyncio.create_task(sandbox.__aenter__())
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await sandbox.close()
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_commit_cancellation_finishes_registry_transaction_and_cleans_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    committed, release = threading.Event(), threading.Event()
    original = CoreEngine.commit

    def delayed_commit(engine: CoreEngine, node_id: str) -> Snapshot:
        snapshot = original(engine, node_id)
        committed.set()
        assert release.wait(5)
        return snapshot

    monkeypatch.setattr(CoreEngine, "commit", delayed_commit)
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with sandbox:
        manager = CheckpointManager(sandbox)
        task = asyncio.create_task(manager.commit("canceled-observer"))
        assert await asyncio.to_thread(committed.wait, 10)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager.registry.latest().node_id == "canceled-observer"
        await manager.rollback_to_latest()
        assert (await sandbox.execute("printf after-canceled-commit")).succeeded
    assert owned_resources(sandbox.namespace) == ([], [])


@pytest.mark.parametrize("failure", ["image", "runtime"])
async def test_actual_creation_failure_reclaims_partial_container(tmp_path: Path, failure: str):
    config = CoreConfig(
        data_dir=tmp_path,
        image="openresearch-missing-core-image:nonexistent"
        if failure == "image"
        else "python:3.12-slim",
        runtime="openresearch-missing-runtime" if failure == "runtime" else "runc",
    )
    sandbox = CoreSandbox(config)
    with pytest.raises(docker.errors.DockerException):
        async with sandbox:
            pytest.fail("an unavailable image/runtime must not start")
    await sandbox.close()
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_declared_volume_image_is_rejected_before_creation(tmp_path: Path):
    seed = CoreSandbox(CoreConfig(data_dir=tmp_path / "seed", image="python:3.12-slim"))
    async with seed:
        with closing(docker.from_env(timeout=30)) as client:
            response = client.api.commit(
                seed.container_id,
                pause=True,
                conf={
                    "Volumes": {"/workspace": {}},
                    "Labels": {"openresearch.test": "core-volume"},
                },
            )
            image_id = response["Id"]
            try:
                blocked = CoreSandbox(CoreConfig(data_dir=tmp_path / "blocked", image=image_id))
                with pytest.raises(SandboxError, match="without declared volumes"):
                    async with blocked:
                        pytest.fail("volume data cannot be claimed by a Docker Commit checkpoint")
                await blocked.close()
                assert owned_resources(blocked.namespace) == ([], [])
            finally:
                client.images.remove(image_id)
    assert owned_resources(seed.namespace) == ([], [])
