"""PDF 网络测试夹具，实际应用不导入这些连接替身。"""

import io
import ssl

import httpcore
from pypdf import PdfWriter

PDF = b"%PDF-1.4\ntransport fixture\n%%EOF"
PUBLIC = "93.184.215.14"


def response(body=PDF, *, status=200, headers=None):
    values = {
        "Content-Type": "application/pdf",
        "Content-Length": str(len(body)),
        **(headers or {}),
    }
    head = f"HTTP/1.1 {status} test\r\n" + "".join(
        f"{key}: {value}\r\n" for key, value in values.items()
    )
    return (head + "\r\n").encode() + body


def small_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class Wire(httpcore.AsyncNetworkBackend):
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.streams = []
        self.tls = []
        self.writes = []

    async def connect_tcp(self, host, port, *args, **kwargs):
        self.calls.append((host, port))
        owner = self

        class Stream(httpcore.AsyncMockStream):
            async def write(self, buffer, **kwargs):
                owner.writes.append(buffer)

            async def start_tls(self, context=None, server_hostname=None, *args, **kwargs):
                context = context or kwargs["ssl_context"]
                assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
                owner.tls.append(server_hostname)
                return self

        stream = Stream(self.replies.pop(0))
        self.streams.append(stream)
        return stream
