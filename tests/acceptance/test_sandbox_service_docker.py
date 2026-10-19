"""真实 Docker 验收与离线协议测试分开，保留唯一模块名。"""

import json
import os

import httpx
import pytest

from openresearch.sandbox.docker import DockerSandbox
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.http import HttpSandbox
from openresearch.sandbox.service import create_sandbox_app

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("OPENRESEARCH_TEST_DOCKER") != "1", reason="Docker opt-in required"
    ),
]


async def test_python_service_runs_real_python_commands_files_and_cleanup(settings):
    native = DockerSandbox(settings)
    app = create_sandbox_app(settings, FallbackSandbox(native))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://service")
    adapter = HttpSandbox(settings, client)
    identifier = await adapter.create("a" * 32, "b" * 32)
    logs = []

    async def on_log(stream, text):
        logs.append(text)

    try:
        await adapter.put_files(identifier, {"probe.txt": b"actual sandbox file"})
        assert await adapter.get_file(identifier, "probe.txt", 1024) == b"actual sandbox file"
        assert (await adapter.image_identity(identifier)).startswith("sha256:")
        result = await adapter.execute(identifier, "print(6 * 7)", on_log)
        assert result.stdout.strip() == "42" and result.exit_code == 0
        response = await client.post(
            f"/api/v1/sandboxes/{identifier}/commands",
            json={"cmd": ["python", "-c", "import sys; print('command-output'); sys.exit(7)"]},
        )
        events = [json.loads(line) for line in response.text.splitlines()]
        final = events[-1]["response"]
        assert final["stdout"].strip() == "command-output" and final["exit_code"] == 7
        assert (await adapter.inventory())[0].id == identifier
    finally:
        await adapter.close(identifier)
        assert await adapter.inventory() == []
        await adapter.shutdown()
