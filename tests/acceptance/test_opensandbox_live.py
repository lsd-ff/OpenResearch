import asyncio
import os

import pytest

from openresearch.config import Settings
from openresearch.errors import SandboxError
from openresearch.sandbox.docker import DockerSandbox
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.opensandbox import OpenSandbox

pytestmark = [
    pytest.mark.docker,
    pytest.mark.external,
    pytest.mark.skipif(
        os.environ.get("OPENRESEARCH_TEST_OPENSANDBOX") != "1",
        reason="requires explicit opt-in and a real configured OpenSandbox server",
    ),
]


async def test_real_opensandbox_fallback_python_files_exit_status_and_cleanup(settings):
    class UnavailableDocker(DockerSandbox):
        async def create(self, plan_id, execution_id):
            raise SandboxError("intentional primary creation failure for fallback verification")

    local = Settings()
    settings.opensandbox_url = os.environ.get(
        "OPENRESEARCH_TEST_OPENSANDBOX_URL", "http://127.0.0.1:18088"
    )
    settings.opensandbox_api_key = local.opensandbox_api_key
    secondary = OpenSandbox(settings)
    adapter = FallbackSandbox(UnavailableDocker(settings), secondary)
    identifier = None
    logs = []

    async def on_log(stream, text):
        logs.append((stream, text))

    try:
        assert await secondary.health()
        identifier = await adapter.create("a" * 32, "b" * 32)
        assert identifier.startswith("os-")
        result = await adapter.execute(
            identifier, "print('actual-opensandbox-' + str(5 * 9))", on_log
        )
        assert result.stdout.strip() == "actual-opensandbox-45" and result.exit_code == 0
        await adapter.put_files(identifier, {"probe.txt": b"real fallback file"})
        assert await adapter.get_file(identifier, "probe.txt", 1024) == b"real fallback file"
        result = await adapter.execute(identifier, "raise SystemExit(7)", on_log)
        assert result.exit_code == 7
        assert (await adapter.inventory())[0].id == identifier
    finally:
        if identifier:
            await adapter.close(identifier)
            async with asyncio.timeout(30):
                for _ in range(120):
                    if not await adapter.inventory():
                        break
                    await asyncio.sleep(0.25)
                else:
                    pytest.fail("OpenSandbox did not finish resource cleanup")
        await adapter.shutdown()
