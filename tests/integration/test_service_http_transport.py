import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openresearch.models.context import Conversation
from openresearch.models.intent import IntentRequest
from openresearch.sandbox.http import HttpSandbox
from openresearch.services.remote_intent import RemoteIntentClient


class ServiceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond({"ok": True})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.respond({"workflow": "data"})

    def respond(self, payload):
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):
        pass


@contextmanager
def running_service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ServiceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


async def test_tokenless_sandbox_service_works_over_real_http_transport(settings):
    with running_service() as address:
        settings.sandbox_url = address
        adapter = HttpSandbox(settings)
        try:
            assert await adapter.health()
        finally:
            await adapter.shutdown()


async def test_tokenless_intent_service_works_over_real_http_transport(settings):
    with running_service() as address:
        settings.intent_service_url = address
        reply = await RemoteIntentClient(settings).classify(
            IntentRequest(snapshot=Conversation(owner_id="a", session_id="s"), message="分析数据")
        )
        assert reply.workflow == "data"
