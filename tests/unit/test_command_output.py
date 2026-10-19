import base64

import pytest

from openresearch.errors import SandboxError
from openresearch.sandbox.command_output import CommandOutput


async def test_output_frames_preserve_whitespace_unicode_and_partial_network_chunks():
    payload = "head\n\n尾部".encode()
    frame = "OPENRESEARCH_STREAM_V1:stdout:" + base64.b64encode(payload).decode() + "\n"
    output = CommandOutput(1024)
    logs = []

    async def on_log(stream, text):
        logs.append((stream, text))

    await output.feed(frame[:25], False, on_log)
    await output.feed(frame[25:], False, on_log)
    await output.feed("OPENRESEARCH_STREAM_V1:stderr:ZXJy", True, on_log)
    result = output.result(7)
    assert result.stdout == "head\n\n尾部" and result.stderr == "err" and result.exit_code == 7
    assert logs == [("stdout", "head\n\n尾部"), ("stderr", "err")]


@pytest.mark.parametrize("frame", ["unframed\n", "OPENRESEARCH_STREAM_V1:stdout:invalid!\n"])
async def test_invalid_output_frames_are_not_treated_as_success(frame):
    output = CommandOutput(10)

    async def on_log(stream, text):
        pass

    with pytest.raises(SandboxError):
        await output.feed(frame, False, on_log)
