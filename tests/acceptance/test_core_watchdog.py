import asyncio
import json
from pathlib import Path

import pytest
from test_core_checkpoints import available_core_image as available_core_image
from test_core_checkpoints import owned_resources
from test_docker_smoke import pytestmark as pytestmark

from openresearch.checkpoint.manager import CheckpointManager
from openresearch.sandbox.core.models import CoreConfig
from openresearch.sandbox.core.session import CoreSandbox
from openresearch.watchdog.models import FailureKind, WatchdogConfig, WatchdogError
from openresearch.watchdog.service import Watchdog


async def test_actual_repeated_failure_circuit_and_explicit_rollback(tmp_path: Path):
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with sandbox:
        manager = CheckpointManager(sandbox)
        assert (
            await sandbox.execute("printf original > value; export CORE_VALUE=stable")
        ).succeeded
        snapshot = await manager.commit("safe")
        watchdog = Watchdog(sandbox, manager, WatchdogConfig(max_retry_window=3))
        command = "printf changed > value; printf 'dependency failed'; (exit 23)"
        for expected in (FailureKind.ENV_CORRUPTED,) * 2 + (FailureKind.CIRCUIT_BROKEN,):
            with pytest.raises(WatchdogError) as captured:
                await watchdog.execute(command, node_id="broken")
            error = captured.value
            assert error.kind == expected and error.result.exit_code == 23
        assert manager.feedback.failure_count == 3
        assert json.loads(manager.get_full_feedback_context())["last_rollback"] is None
        assert (await sandbox.execute("cat value")).output == "changed"
        before = sandbox.container_id
        receipt = await watchdog.rollback_to_safe_state(snapshot.image_id)
        assert receipt.old_container_id == before != sandbox.container_id
        assert (await watchdog.execute("cat value")).output == "original"
        context = json.loads(manager.get_full_feedback_context())
        assert context["last_rollback"]["node_id"] == "safe"
        events = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (tmp_path / "watchdog").glob("*.json")
        ]
        assert sum(event["status"] == "failed" for event in events) == 3
    assert owned_resources(sandbox.namespace) == ([], [])


@pytest.mark.parametrize("ignore_interrupt", [False, True])
async def test_actual_timeout_reports_partial_output_and_checkpoint_can_recover(
    tmp_path: Path, ignore_interrupt: bool
):
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with sandbox:
        manager = CheckpointManager(sandbox)
        snapshot = await manager.commit("before-timeout")
        watchdog = Watchdog(sandbox, manager, WatchdogConfig(command_timeout_seconds=0.2))
        command = ("trap '' INT; " if ignore_interrupt else "") + "printf started; sleep 60"
        with pytest.raises(WatchdogError) as captured:
            await watchdog.execute(command)
        error = captured.value
        assert error.kind == FailureKind.TIMEOUT_HANG
        assert error.result.timed_out and "started" in error.result.output
        record = json.loads(
            error.result.audit_path.with_suffix(".json").read_text(encoding="utf-8")
        )
        assert record["timed_out"] and record["exit_code"] is None
        await watchdog.rollback_to_safe_state(snapshot.image_id)
        assert (await sandbox.execute("printf after-timeout")).output == "after-timeout"
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_success_words_and_cancellation_do_not_record_failures(tmp_path: Path):
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with sandbox:
        manager = CheckpointManager(sandbox)
        watchdog = Watchdog(sandbox, manager, WatchdogConfig(max_retry_window=2))
        for _ in range(3):
            assert (
                await watchdog.execute("printf 'error: old cache; installation finished'")
            ).succeeded
        task = asyncio.create_task(watchdog.execute("printf waiting; sleep 60"))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager.feedback.failure_count == 0
        assert (await watchdog.execute("printf after-cancel")).succeeded
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_crash_exit_and_output_budget_remain_failures(tmp_path: Path):
    sandbox = CoreSandbox(
        CoreConfig(data_dir=tmp_path, image="python:3.12-slim", max_output_bytes=4096)
    )
    async with sandbox:
        manager = CheckpointManager(sandbox)
        watchdog = Watchdog(sandbox, manager)
        with pytest.raises(WatchdogError) as captured:
            await watchdog.execute(
                "ulimit -c 0; python -c 'import os, signal; os.kill(os.getpid(), signal.SIGSEGV)'"
            )
        assert captured.value.kind == FailureKind.ENV_CORRUPTED
        assert captured.value.result.exit_code == 139
        with pytest.raises(WatchdogError) as captured:
            await watchdog.execute("python -c 'print(\"x\" * 1000000)'")
        assert captured.value.kind == FailureKind.OUTPUT_LIMIT
        assert captured.value.result.output_limited
        assert (await watchdog.execute("printf healthy")).succeeded
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_shell_disconnect_keeps_partial_evidence(tmp_path: Path):
    sandbox = CoreSandbox(CoreConfig(data_dir=tmp_path, image="python:3.12-slim"))
    async with sandbox:
        watchdog = Watchdog(sandbox)
        manager = watchdog.checkpoints
        with pytest.raises(WatchdogError) as captured:
            await watchdog.execute("printf before-exit; exit 9")
        error = captured.value
        assert error.kind == FailureKind.ENV_CORRUPTED
        assert error.result.exit_code is None and "before-exit" in error.result.output
        record = json.loads(
            error.result.audit_path.with_suffix(".json").read_text(encoding="utf-8")
        )
        assert record["partial"] and record["error_type"] == "TerminalConnectionError"
        assert manager.feedback.failure_count == 1
    assert owned_resources(sandbox.namespace) == ([], [])


async def test_actual_close_preserves_other_session_with_the_same_namespace(tmp_path: Path):
    config = CoreConfig(data_dir=tmp_path, image="python:3.12-slim")
    left, right = CoreSandbox(config), CoreSandbox(config)
    async with left, right:
        left_manager, right_manager = CheckpointManager(left), CheckpointManager(right)
        await left_manager.commit("left-safe")
        snapshot = await right_manager.commit("right-safe")
        await Watchdog(left, left_manager).close()
        containers, images = owned_resources(right.namespace)
        assert containers == [right.container_id] and images == [snapshot.image_id]
        assert (await right.execute("printf other-session-alive")).succeeded
        await right_manager.rollback(snapshot.image_id)
        assert (await right.execute("printf after-other-close")).succeeded
    assert owned_resources(right.namespace) == ([], [])
