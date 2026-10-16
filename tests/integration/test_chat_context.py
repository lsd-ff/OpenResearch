import io
import json

import httpx
import pytest
from conftest import FakeSandbox
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openresearch.api.app import create_app
from openresearch.config import Settings
from openresearch.llm.client import LLMClient
from openresearch.models.context import Identity
from openresearch.models.documents import PaperCorpus, ParsedDocument, ParsedPage
from openresearch.models.execution import ExecutionLease, TaskCompletion
from openresearch.models.runtime import TaskResult
from openresearch.models.submissions import PlanSubmission
from openresearch.services.plans import PlanService
from openresearch.services.uploads import UploadService
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository
from openresearch.storage.upload_files import UploadSubmission


def seed_source(settings: Settings, repository: Repository, files: ArtifactStore) -> str:
    identity = Identity("alice", "research")
    upload = UploadService(settings, repository).create(
        UploadSubmission("notes.md", io.BytesIO(b"Observed accuracy was 0.75."), identity)
    )
    plan = PlanService(settings, repository).create(
        PlanSubmission("extract source", identity, attachments=[upload.id], workflow="paper")
    )
    repository.queue_plan(plan.id, "alice")
    claimed = repository.claim_next("test", 30, 1)
    assert claimed is not None
    lease = ExecutionLease.for_task(*claimed, "test")
    corpus = PaperCorpus(
        documents=[
            ParsedDocument(
                source=upload.source(),
                pages=[ParsedPage(number=1, text="Observed accuracy was 0.75.")],
            )
        ]
    )
    result = TaskResult(
        outputs={"parsed_paper": corpus.model_dump_json(), "source_index": '{"links":[]}'}
    )
    artifacts = [files.write(lease, key, content) for key, content in result.outputs.items()]
    repository.finish(lease, TaskCompletion(result, artifacts))
    return upload.id


def provider_reply(response: str, citations: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "response": response,
                                "citations": citations,
                            }
                        )
                    }
                }
            ]
        },
    )


def test_chat_restores_history_and_cites_current_session_sources(
    settings: Settings, repository: Repository, files: ArtifactStore, sandbox: FakeSandbox
) -> None:
    source_id = seed_source(settings, repository, files)
    settings.llm_model, settings.llm_api_key = "test-model", SecretStr("test-key")
    owner = {"X-User-Id": "alice", "X-Session-Id": "research"}
    citation = {"upload_id": source_id, "page": 1, "quote": "Observed accuracy was 0.75."}

    def provider(request: httpx.Request) -> httpx.Response:
        assert source_id in request.content.decode()
        return provider_reply("Accuracy was 0.75.", [citation])

    with TestClient(create_app(settings, repository, sandbox)) as client:
        client.app.state.runtime.chat.llm = LLMClient(settings, httpx.MockTransport(provider))
        reply = client.post("/api/chat", headers=owner, json={"message": "What was the accuracy?"})
        assert reply.status_code == 200 and reply.json()["citations"] == [citation]

    def next_provider(request: httpx.Request) -> httpx.Response:
        data = request.content.decode()
        assert "What was the accuracy?" in data and "Accuracy was 0.75." in data
        return provider_reply("The previous response cited page one.", [citation])

    with TestClient(create_app(settings, repository, sandbox)) as client:
        client.app.state.runtime.chat.llm = LLMClient(settings, httpx.MockTransport(next_provider))
        assert (
            client.post("/api/chat", headers=owner, json={"message": "Which page?"}).status_code
            == 200
        )
        assert len(client.get("/api/session", headers=owner).json()["turns"]) == 4
        assert (
            client.get("/api/session", headers={**owner, "X-Session-Id": "other"}).json()["turns"]
            == []
        )


@pytest.mark.parametrize("bad_source", ["unknown_id", "unknown_page", "invented_quote"])
def test_fabricated_chat_citation_is_rejected(
    bad_source: str,
    settings: Settings,
    repository: Repository,
    files: ArtifactStore,
    sandbox: FakeSandbox,
) -> None:
    source_id = seed_source(settings, repository, files)
    settings.llm_model, settings.llm_api_key = "test-model", SecretStr("test-key")
    citation = {
        "upload_id": "foreign" if bad_source == "unknown_id" else source_id,
        "page": 7 if bad_source == "unknown_page" else 1,
        "quote": "Accuracy is perfect" if bad_source == "invented_quote" else "0.75",
    }
    with TestClient(create_app(settings, repository, sandbox)) as client:
        client.app.state.runtime.chat.llm = LLMClient(
            settings, httpx.MockTransport(lambda _: provider_reply("Fabricated answer", [citation]))
        )
        headers = {"X-User-Id": "alice", "X-Session-Id": "research"}
        assert (
            client.post("/api/chat", headers=headers, json={"message": "accuracy"}).status_code
            == 422
        )
        assert client.get("/api/session", headers=headers).json()["turns"] == []


def test_missing_model_is_explicit_and_does_not_write_fake_history(
    settings: Settings, repository: Repository, sandbox: FakeSandbox
) -> None:
    with TestClient(create_app(settings, repository, sandbox)) as client:
        assert client.post("/api/chat", json={"message": "hello"}).status_code == 503
        assert client.get("/api/session").json()["turns"] == []
