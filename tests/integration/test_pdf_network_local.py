import asyncio

import pytest

from openresearch.errors import ContractError
from openresearch.sources.remote_pdf import RemotePdfReader


async def test_real_loopback_listener_is_not_contacted_by_pdf_download(settings):
    accepted = []

    async def connected(reader, writer):
        accepted.append(True)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(connected, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(ContractError, match="private"):
            await RemotePdfReader(settings).fetch(f"http://127.0.0.1:{port}/private.pdf")
        assert not accepted
    finally:
        server.close()
        await server.wait_closed()
