"""公共 PDF 下载在 API、规划和附件归属中的集成契约。"""

import hashlib

import httpcore
import pytest
from fastapi.testclient import TestClient
from pdf_network import PUBLIC, Wire, response, small_pdf
from pydantic import SecretStr

from openresearch.api.app import create_app
from openresearch.llm.client import LLMClient
from openresearch.models.context import Identity
from openresearch.models.planning import IntentDraft
from openresearch.services.intent import looks_like_task
from openresearch.sources import public_network


def network(monkeypatch, raw, *, private=False):
    wire = Wire([[response(raw)]])

    async def resolve(host, port):
        return ["127.0.0.1" if private else PUBLIC]

    monkeypatch.setattr(public_network, "resolve_addresses", resolve)
    monkeypatch.setattr(httpcore, "AnyIOBackend", lambda: wire)
    return wire


def test_pdf_proxy_accepts_a_public_https_source_without_leaking_ownership(
    settings, repository, sandbox, monkeypatch
):
    raw = small_pdf()
    wire = network(monkeypatch, raw)
    settings.api_token = SecretStr("browser-api-only-secret")
    with TestClient(create_app(settings, repository, sandbox)) as client:
        result = client.get(
            "/api/pdf-proxy",
            params={"url": "https://paper.example/download?id=3"},
            headers={"Authorization": "Bearer browser-api-only-secret"},
        )
        assert result.status_code == 200 and result.content == raw
        assert result.headers["content-type"] == "application/pdf"
        assert result.headers["x-content-type-options"] == "nosniff"
        assert wire.calls == [(PUBLIC, 443)]
        assert b"browser-api-only-secret" not in b"".join(wire.writes)


def test_pdf_proxy_rejects_private_dns_before_opening_any_socket(
    settings, repository, sandbox, monkeypatch
):
    wire = network(monkeypatch, small_pdf(), private=True)
    with TestClient(create_app(settings, repository, sandbox)) as client:
        result = client.get("/api/pdf-proxy", params={"url": "http://internal.example/a.pdf"})
        assert result.status_code == 422 and "non-public" in result.json()["error"]
    assert not wire.calls


def test_chat_can_download_a_user_supplied_public_pdf_as_an_owned_paper_attachment(
    settings, repository, sandbox, monkeypatch
):
    url = "https://paper.example/download?id=3"
    raw = small_pdf()
    wire = network(monkeypatch, raw)
    calls = []

    async def proposal(self, messages, response_type):
        calls.append(response_type)
        assert response_type is IntentDraft
        return IntentDraft(workflow="paper", paper_url=url)

    monkeypatch.setattr(LLMClient, "request_typed", proposal)
    owner = {"X-User-Id": "reader", "X-Session-Id": "remote"}
    with TestClient(create_app(settings, repository, sandbox)) as client:
        result = client.post("/api/messages", json={"message": "阅读 " + url}, headers=owner)
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["type"] == "plan"
        graph = payload["plan_graph"]
        assert len(graph["attachments"]) == 1
        upload_id = graph["attachments"][0]
        record = repository.uploads.get(upload_id, "reader")
        assert payload["papers"][0]["id"] == upload_id
        assert payload["papers"][0]["content_url"] == f"/api/uploads/{upload_id}/content"
        assert "location" not in payload["papers"][0] and "owner_id" not in payload["papers"][0]
        assert record.name == "download.pdf" and record.sha256 == hashlib.sha256(raw).hexdigest()
        assert client.get(f"/api/uploads/{upload_id}/content", headers=owner).content == raw
        assert (
            client.get(
                f"/api/uploads/{upload_id}/content", headers={"X-User-Id": "other"}
            ).status_code
            == 404
        )
        state = repository.sessions.get(Identity("reader", "remote"))
        assert upload_id in state.uploads
    assert calls == [IntentDraft] and wire.calls == [(PUBLIC, 443)]


@pytest.mark.parametrize(
    "message",
    [
        "阅读 https://paper.example/paper.pdf",
        "总结一下 https://paper.example/download?id=1",
        "Read https://paper.example/file.pdf",
    ],
)
def test_explicit_link_reading_reaches_the_intent_classifier(message):
    assert looks_like_task(message)
