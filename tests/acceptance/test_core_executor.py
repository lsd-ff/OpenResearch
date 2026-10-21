import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import docker
import pytest
from test_core_checkpoints import available_core_image as available_core_image
from test_docker_smoke import pytestmark as pytestmark

from openresearch.executor.engine import Engine
from openresearch.executor.gates import Gatekeeper, ResourceConfig
from openresearch.executor.models import DAG, ExecutorConfig, Node
from openresearch.executor.receipts import ExecutionError
from openresearch.sandbox.core.models import CoreConfig


def core_config(tmp_path: Path) -> CoreConfig:
    return CoreConfig(data_dir=tmp_path, image="python:3.12-slim")


def assert_no_core_resources():
    client = docker.from_env(timeout=10)
    try:
        filters = {"label": "openresearch.core.managed=true"}
        assert client.containers.list(all=True, filters=filters) == []
        assert client.images.list(all=True, filters=filters) == []
    finally:
        client.close()


@contextmanager
def concurrent_barrier(parties=2):
    import os

    arrived = threading.Event()
    callers = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                callers.append(self.path)
                if len(callers) >= parties:
                    arrived.set()
            if not arrived.wait(15):
                self.send_error(504)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ready")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = docker.from_env(timeout=10)
    try:
        host = "host.docker.internal"
        if os.name != "nt":
            host = client.networks.get("bridge").attrs["IPAM"]["Config"][0]["Gateway"]
        yield f"http://{host}:{server.server_port}", callers, arrived
    finally:
        client.close()
        arrived.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def reach_barrier(url: str, label: str) -> str:
    return (
        "python -c 'import socket,urllib.request; "
        f'print(urllib.request.urlopen("{url}/{label}/"+socket.gethostname(),timeout=20).read())'
        "'"
    )


async def test_actual_racing_strategies_use_distinct_containers_and_drain_loser(tmp_path: Path):
    config = ExecutorConfig(sandbox=core_config(tmp_path).model_copy(update={"network": "bridge"}))
    with concurrent_barrier() as (url, callers, _):
        commands = [
            reach_barrier(url, "slow") + " && sleep 60 && touch loser",
            reach_barrier(url, "winner") + " && printf winner > selected",
        ]
        async with Engine(config) as engine:
            receipt = await engine.execute_graph(DAG(nodes=[Node(id="race", commands=commands)]))
            assert receipt.status == "completed"
            attempts = receipt.nodes[0].attempts
            assert {item.status for item in attempts} == {"published", "canceled"}
            assert len({item.container_id for item in attempts}) == 2
            assert len(callers) == 2 and len({path.rsplit("/", 1)[1] for path in callers}) == 2
            async with await engine.snapshot() as snapshot, snapshot.fork() as reader:
                result = await reader.execute("cat selected; test ! -e loser")
                assert result.succeeded and result.output == "winner"
            assert engine.gates.io_usage.in_use == engine.gates.io_usage.waiting == 0
    assert_no_core_resources()


async def test_actual_parallel_diamond_rebases_without_losing_sibling_state(tmp_path: Path):
    config = ExecutorConfig(sandbox=core_config(tmp_path).model_copy(update={"network": "bridge"}))
    with concurrent_barrier() as (url, callers, _):
        graph = DAG(
            nodes=[
                Node(
                    id="left",
                    commands=[
                        "{ test -f right || "
                        + reach_barrier(url, "left")
                        + "; } && printf left > left && export LEFT=left"
                    ],
                ),
                Node(
                    id="right",
                    commands=[
                        "{ test -f left || "
                        + reach_barrier(url, "right")
                        + "; } && printf right > right && export RIGHT=right"
                    ],
                ),
                Node(
                    id="join",
                    commands=['test "$LEFT:$RIGHT" = left:right && cat left right > joined'],
                    dependencies=["left", "right"],
                ),
            ]
        )
        async with Engine(config) as engine:
            receipt = await engine.execute_graph(graph)
            assert receipt.status == "completed" and receipt.version == 3
            left, right, join = receipt.nodes
            assert sorted([left.rounds, right.rounds]) == [1, 2]
            assert (
                sum(item.status == "superseded" for n in receipt.nodes for item in n.attempts) == 1
            )
            assert len(callers) == 2
            assert join.published_version == 3
            async with await engine.snapshot() as snapshot, snapshot.fork() as reader:
                result = await reader.execute("cat joined")
                assert result.succeeded and result.output == "leftright"
            stored = json.loads(receipt.audit_path.read_text(encoding="utf-8"))
            assert stored["version"] == 3 and stored["status"] == "completed"
    assert_no_core_resources()


async def test_actual_failed_candidate_writes_do_not_leak_and_empty_output_succeeds(tmp_path: Path):
    graph = DAG(
        nodes=[
            Node(id="choose", commands=["printf poison > bad; (exit 7)", "sleep 0.2; true"]),
            Node(id="check", commands=["test ! -e bad"], dependencies=["choose"]),
        ]
    )
    async with Engine(ExecutorConfig(sandbox=core_config(tmp_path))) as engine:
        receipt = await engine.execute_graph(graph)
        assert receipt.status == "completed"
        failed = next(item for item in receipt.nodes[0].attempts if item.status == "failed")
        assert failed.result.exit_code == 7
        assert receipt.nodes[-1].attempts[0].result.summary == ""
    assert_no_core_resources()


async def test_actual_all_failed_strategies_block_descendants_and_keep_base(tmp_path: Path):
    gates = Gatekeeper(ResourceConfig(max_concurrent_io=2, max_gpu_tasks=0))
    graph = DAG(
        nodes=[
            Node(id="fail", commands=["touch bad; (exit 3)", "touch bad; (exit 4)"]),
            Node(id="blocked", commands=["touch downstream"], dependencies=["fail"]),
        ]
    )
    async with Engine(ExecutorConfig(sandbox=core_config(tmp_path)), gates=gates) as engine:
        with pytest.raises(ExecutionError) as caught:
            await engine.execute_graph(graph)
        receipt = caught.value.receipt
        assert receipt.status == "failed" and receipt.version == 0
        assert [item.status for item in receipt.nodes] == ["failed", "blocked"]
        assert {item.result.exit_code for item in receipt.nodes[0].attempts} == {3, 4}
        async with await engine.snapshot() as snapshot, snapshot.fork() as reader:
            assert (await reader.execute("test ! -e bad && test ! -e downstream")).succeeded
        assert gates.io_usage.in_use == gates.io_usage.waiting == 0
    assert_no_core_resources()


async def test_actual_cancel_drains_active_command_and_returns_all_io_weight(tmp_path: Path):
    config = ExecutorConfig(sandbox=core_config(tmp_path).model_copy(update={"network": "bridge"}))
    with concurrent_barrier(1) as (url, _, arrived):
        graph = DAG(
            nodes=[Node(id="cancel", commands=[reach_barrier(url, "started") + "; sleep 60"])]
        )
        async with Engine(config) as engine:
            task = asyncio.create_task(engine.execute_graph(graph))
            try:
                assert await asyncio.to_thread(arrived.wait, 30)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert engine.last_receipt.status == "canceled"
                assert engine.last_receipt.nodes[0].status == "canceled"
                assert engine.gates.io_usage.in_use == engine.gates.io_usage.waiting == 0
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert_no_core_resources()


async def test_actual_timeout_preserves_node_deadline_and_audits_failure(tmp_path: Path):
    config = ExecutorConfig(sandbox=core_config(tmp_path), node_timeout_seconds=5)
    async with Engine(config) as engine:
        with pytest.raises(ExecutionError) as caught:
            await engine.execute_graph(
                DAG(nodes=[Node(id="slow", commands=["printf started; sleep 60"])])
            )
        assert caught.value.receipt.nodes[0].status == "timed_out"
        assert caught.value.receipt.nodes[0].attempts[0].status == "canceled"
        assert engine.gates.io_usage.in_use == 0
    assert_no_core_resources()


async def test_actual_io_queue_cancellation_releases_weighted_capacity(tmp_path: Path):
    gates = Gatekeeper(ResourceConfig(max_concurrent_io=2, max_gpu_tasks=0))
    graph = DAG(nodes=[Node(id="weighted", io_weight=2, commands=["true", "sleep 60", "sleep 61"])])
    async with Engine(ExecutorConfig(sandbox=core_config(tmp_path)), gates=gates) as engine:
        receipt = await engine.execute_graph(graph)
        assert receipt.status == "completed"
        assert [item.status for item in receipt.nodes[0].attempts] == [
            "published",
            "canceled",
            "canceled",
        ]
        assert gates.io_usage.in_use == gates.io_usage.waiting == 0
    assert_no_core_resources()


async def test_actual_attempt_budget_stops_before_admitting_candidates(tmp_path: Path):
    config = ExecutorConfig(sandbox=core_config(tmp_path), max_attempts=1)
    async with Engine(config) as engine:
        with pytest.raises(ExecutionError) as caught:
            await engine.execute_graph(
                DAG(nodes=[Node(id="budget", commands=["true", "printf x"])])
            )
        record = caught.value.receipt.nodes[0]
        assert record.error_type == "AttemptBudgetExceeded" and record.attempts == []
        assert caught.value.receipt.version == 0
    assert_no_core_resources()


async def test_actual_rebase_keeps_attempt_budget_and_does_not_regenerate_strategies(
    tmp_path: Path,
):
    with concurrent_barrier() as (url, callers, _):

        class FixtureAgent:
            def __init__(self):
                self.calls = []

            async def plan(self, _goal):
                raise AssertionError("this test supplies the original immutable DAG")

            async def generate_strategies(self, instruction):
                self.calls.append(instruction)
                other = "right" if instruction == "left" else "left"
                command = (
                    f"{{ test -f {other} || "
                    + reach_barrier(url, instruction)
                    + f"; }} && printf {instruction} > {instruction}"
                )
                return command, "(exit 9)"

        agent = FixtureAgent()
        config = ExecutorConfig(
            sandbox=core_config(tmp_path).model_copy(update={"network": "bridge"}),
            max_attempts_per_node=2,
        )
        async with Engine(config, agent=agent) as engine:
            with pytest.raises(ExecutionError) as caught:
                await engine.execute_graph(DAG(nodes=[Node(id="left"), Node(id="right")]))
            receipt = caught.value.receipt
            assert receipt.version == 1 and receipt.status == "failed"
            assert sorted(agent.calls) == ["left", "right"] and len(callers) == 2
            assert all(len(node.attempts) == 2 for node in receipt.nodes)
            assert sum(node.error_type == "AttemptBudgetExceeded" for node in receipt.nodes) == 1
            assert sum(a.status == "superseded" for n in receipt.nodes for a in n.attempts) == 1
    assert_no_core_resources()


async def test_actual_explicit_engine_close_cancels_and_drains_running_graph(tmp_path: Path):
    config = ExecutorConfig(sandbox=core_config(tmp_path).model_copy(update={"network": "bridge"}))
    with concurrent_barrier(1) as (url, _, arrived):
        async with Engine(config) as engine:
            graph = DAG(
                nodes=[Node(id="close", commands=[reach_barrier(url, "started") + " && sleep 60"])]
            )
            task = asyncio.create_task(engine.execute_graph(graph))
            try:
                assert await asyncio.to_thread(arrived.wait, 30)
                await engine.close()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert engine.last_receipt.status == "canceled"
                assert engine.gates.io_usage.in_use == 0
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert_no_core_resources()


async def test_actual_overall_deadline_covers_seed_creation_and_drains_it(tmp_path: Path):
    config = ExecutorConfig(sandbox=core_config(tmp_path), graph_timeout_seconds=0.05)
    async with Engine(config) as engine:
        with pytest.raises(ExecutionError) as caught:
            await engine.execute_graph(DAG(nodes=[Node(id="deadline", commands=["sleep 60"])]))
        assert caught.value.receipt.status == "timed_out"
        assert caught.value.receipt.nodes[0].status in {"blocked", "canceled"}
    assert_no_core_resources()


async def test_actual_cleanup_error_keeps_io_until_container_is_removed(
    tmp_path: Path, monkeypatch
):
    from openresearch.errors import SandboxError
    from openresearch.sandbox.core.session import CoreSandbox

    gates = Gatekeeper(ResourceConfig(max_concurrent_io=2, max_gpu_tasks=0))
    original = CoreSandbox.close
    observations = []

    async def fail_once(sandbox):
        if (
            sandbox.container_id
            and sandbox.config.data_dir.parent.name == "attempts"
            and not observations
        ):
            observations.append((gates.io_usage.in_use, sandbox.container_id))
            client = docker.from_env(timeout=10)
            try:
                assert client.containers.get(sandbox.container_id).status == "running"
            finally:
                client.close()
            raise SandboxError("injected temporary container removal failure")
        return await original(sandbox)

    monkeypatch.setattr(CoreSandbox, "close", fail_once)
    graph = DAG(nodes=[Node(id="cleanup", io_weight=2, commands=["true", "sleep 60"])])
    async with Engine(ExecutorConfig(sandbox=core_config(tmp_path)), gates=gates) as engine:
        with pytest.raises(ExecutionError) as caught:
            await engine.execute_graph(graph)
        receipt = caught.value.receipt
        assert len(observations) == 1 and observations[0][0] == 2
        assert receipt.version == 0 and receipt.status == "failed"
        assert sum(item.started_at is not None for item in receipt.nodes[0].attempts) == 1
        assert receipt.nodes[0].attempts[0].result.exit_code == 0
        assert gates.io_usage.in_use == gates.io_usage.waiting == 0
    assert_no_core_resources()


async def test_actual_executor_preserves_evidence_under_deep_data_directory(tmp_path: Path):
    root = tmp_path / ("first" * 12) / ("second" * 10) / ("third" * 12)
    async with Engine(ExecutorConfig(sandbox=core_config(root))) as engine:
        receipt = await engine.execute_graph(DAG(nodes=[Node(id="deep", commands=["printf deep"])]))
        assert receipt.status == "completed" and receipt.audit_path.is_file()
        result = receipt.nodes[0].attempts[0].result
        assert len(str(result.audit_path)) > 260
        assert result.audit_path.read_bytes() == b"deep"
    assert_no_core_resources()
