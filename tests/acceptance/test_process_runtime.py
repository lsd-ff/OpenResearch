import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import httpx
import pytest
from test_docker_smoke import pytestmark as pytestmark


class RuntimeProcesses:
    def __init__(self, directory: Path, stack: ExitStack) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.directory = directory
        self.stack = stack
        self.url = f"http://127.0.0.1:{port}"
        self.environment = {
            **os.environ,
            "OPENRESEARCH_DATA_DIR": str(directory / "data"),
            "OPENRESEARCH_HOST": "127.0.0.1",
            "OPENRESEARCH_PORT": str(port),
            "OPENRESEARCH_SANDBOX_BACKEND": "docker",
            "OPENRESEARCH_API_TOKEN": "test-key",
            "OPENRESEARCH_LLM_API_KEY": "",
            "OPENRESEARCH_REQUIRE_APPROVAL": "false",
            "OPENRESEARCH_POLL_INTERVAL_SECONDS": "0.05",
            "OPENRESEARCH_LEASE_SECONDS": "30",
        }
        self.api: subprocess.Popen[bytes] | None = None

    def start(self, role: str) -> subprocess.Popen[bytes]:
        log = self.stack.enter_context((self.directory / f"{role}.log").open("ab"))
        process = subprocess.Popen(
            [sys.executable, "-m", "openresearch.cli", role],
            cwd=self.directory,
            env=self.environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self.stack.callback(stop_process, process)
        if role == "serve":
            self.api = process
        return process

    def restart_api(self) -> None:
        assert self.api is not None
        stop_process(self.api)
        self.start("serve")


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@pytest.fixture
def processes(tmp_path: Path) -> Iterator[RuntimeProcesses]:
    with ExitStack() as stack:
        runtime = RuntimeProcesses(tmp_path, stack)
        runtime.start("serve")
        runtime.start("worker")
        yield runtime


def wait_for_api(client: httpx.Client, runtime: RuntimeProcesses) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert runtime.api is not None and runtime.api.poll() is None, (
            runtime.directory / "serve.log"
        ).read_text(errors="replace")
        try:
            response = client.get("/api/health")
            if response.status_code == 200 and response.json()["ok"]:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise AssertionError("API did not become healthy")


def wait_for_completion(client: httpx.Client, plan_id: str) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"/api/plans/{plan_id}")
        response.raise_for_status()
        graph = response.json()["plan_graph"]
        if graph["status"] in {"completed", "failed", "canceled"}:
            assert graph["status"] == "completed", graph
            return graph
        time.sleep(0.1)
    raise AssertionError("worker did not complete the experiment")


def test_api_and_worker_share_persistent_state(processes: RuntimeProcesses) -> None:
    with httpx.Client(
        base_url=processes.url,
        timeout=5,
        headers={"Authorization": "Bearer test-key", "X-User-Id": "alice"},
    ) as client:
        wait_for_api(client, processes)
        created = client.post("/api/plan", json={"intent": "smoke"})
        created.raise_for_status()
        plan_id = created.json()["plan_graph"]["id"]
        assert client.post(f"/api/plans/{plan_id}/execute").status_code == 202
        graph = wait_for_completion(client, plan_id)
        artifact_id = graph["artifacts"]["run_metrics"]["id"]
        content = client.get(f"/api/plans/{plan_id}/artifacts/{artifact_id}/content")
        content.raise_for_status()
        assert json.loads(json.loads(content.text)["stdout"])["sum"] == 15
        history = client.get(f"/api/plans/{plan_id}/events").json()
        cursor = history["next_cursor"]
        assert history["events"][-1]["event_type"] == "plan_completed"
        stream = client.get(f"/api/plans/{plan_id}/stream")
        stream.raise_for_status()
        identifiers = [
            int(line[4:]) for line in stream.text.splitlines() if line.startswith("id: ")
        ]
        assert identifiers == sorted(set(identifiers))
        assert identifiers[-1] == cursor
        processes.restart_api()
        wait_for_api(client, processes)
        assert client.get(f"/api/plans/{plan_id}").json()["plan_graph"]["status"] == "completed"
        replay = client.get(f"/api/plans/{plan_id}/events", params={"after": cursor})
        assert replay.json()["events"] == []
