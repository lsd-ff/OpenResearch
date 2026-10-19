import json

import httpx
import pytest

from openresearch.errors import SandboxError
from openresearch.sandbox.opensandbox import OpenSandbox


def lifecycle(settings, mode="success"):
    calls = []
    metadata = {}

    def handle(request):
        calls.append(request)
        path = request.url.path
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "POST" and path == "/v1/sandboxes":
            value = json.loads(request.content)
            assert value["networkPolicy"]["defaultAction"] == "deny"
            assert value["resourceLimits"]["memory"] == "268435456"
            metadata.update(value["metadata"])
            return httpx.Response(202, json={"id": "test-id"})
        if path.endswith("/endpoints/44772"):
            endpoint = (
                "https://foreign.example/secret"
                if mode == "foreign_endpoint"
                else "http://os.test/proxy/test-id/44772"
            )
            return httpx.Response(
                200, json={"endpoint": endpoint, "headers": {"x-execd-access": "test"}}
            )
        if path.endswith("/command/status/cmd-1"):
            return httpx.Response(
                200, json={"running": False, "exit_code": 7 if mode == "exit" else 0}
            )
        if path.endswith("/command"):
            arguments = json.loads(request.content)
            assert arguments["argv"][0] == "python" and arguments["uid"] == 65534
            events = [
                {"type": "init", "text": "cmd-1"},
                {"type": "stdout", "text": "actual-stream\n"},
            ]
            if mode == "exit":
                events.append({"type": "error", "error": {"ename": "CommandExecError"}})
            if mode != "truncated":
                events.append({"type": "execution_complete"})
            return httpx.Response(
                200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
            )
        if path == "/v1/sandboxes":
            return httpx.Response(200, json={"items": [{"id": "test-id", "metadata": metadata}]})
        return httpx.Response(
            200, json={"id": "test-id", "status": {"state": "Running"}, "metadata": metadata}
        )

    client = httpx.AsyncClient(base_url="http://os.test/", transport=httpx.MockTransport(handle))
    return OpenSandbox(settings, client), calls


@pytest.mark.parametrize("mode", ["success", "exit"])
async def test_lifecycle_and_execd_use_real_contract_and_terminal_exit_code(settings, mode):
    adapter, calls = lifecycle(settings, mode)
    logs = []

    async def on_log(stream, text):
        logs.append((stream, text))

    try:
        identifier = await adapter.create("a" * 32, "b" * 32)
        result = await adapter.execute(identifier, "print('actual-stream')", on_log)
        assert result.stdout == "actual-stream\n"
        assert result.exit_code == (7 if mode == "exit" else 0)
        assert logs == [("stdout", "actual-stream\n")]
        inventory = await adapter.inventory()
        assert inventory[0].execution_id == "b" * 32
        await adapter.close(identifier)
        assert calls[-1].method == "DELETE"
    finally:
        await adapter.shutdown()


@pytest.mark.parametrize("mode", ["truncated", "foreign_endpoint"])
async def test_adapter_rejects_incomplete_execution_and_credential_redirects(settings, mode):
    adapter, calls = lifecycle(settings, mode)

    async def on_log(stream, text):
        pass

    try:
        await adapter.create("a" * 32, "b" * 32)
        with pytest.raises(SandboxError):
            await adapter.execute("test-id", "print(1)", on_log)
        assert all(call.url.host == "os.test" for call in calls)
    finally:
        await adapter.shutdown()
