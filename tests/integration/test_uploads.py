from pathlib import Path

from conftest import FakeSandbox
from fastapi.testclient import TestClient

from openresearch.api.app import create_app
from openresearch.config import Settings
from openresearch.storage.repository import Repository


def test_upload_survives_restart_and_enforces_owner(
    settings: Settings, repository: Repository, sandbox: FakeSandbox
) -> None:
    owner = {"X-User-Id": "alice", "X-Session-Id": "research"}
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/uploads",
            files={"file": ("../notes.md", b"# Reproducible result", "text/plain")},
            headers=owner,
        )
        assert response.status_code == 201
        uploaded = response.json()
        assert uploaded["name"] == "notes.md"
        assert "location" not in uploaded and "owner_id" not in uploaded
        assert (
            client.get(uploaded["content_url"], headers=owner).content == b"# Reproducible result"
        )
        assert client.get(uploaded["content_url"], headers={"X-User-Id": "bob"}).status_code == 404
    with TestClient(create_app(settings, repository, sandbox)) as client:
        assert client.get(uploaded["content_url"], headers=owner).status_code == 200
        context = client.get("/api/session", headers=owner).json()
        assert context["uploads"] == [uploaded["id"]]
        assert (
            client.get("/api/session", headers={**owner, "X-Session-Id": "other"}).json()["uploads"]
            == []
        )


def test_upload_content_type_size_and_hash_are_checked(
    settings: Settings, repository: Repository, sandbox: FakeSandbox
) -> None:
    settings.upload_max_bytes = 1024
    with TestClient(create_app(settings, repository, sandbox)) as client:
        assert client.post("/api/uploads", files={"file": ("x.exe", b"binary")}).status_code == 415
        assert client.post("/api/uploads", files={"file": ("x.pdf", b"not pdf")}).status_code == 415
        assert client.post("/api/uploads", files={"file": ("x.txt", b"")}).status_code == 422
        assert (
            client.post("/api/uploads", files={"file": ("x.txt", b"x" * 1025)}).status_code == 413
        )
        upload = client.post("/api/uploads", files={"file": ("x.txt", b"abc")}).json()
        record = repository.uploads.get(upload["id"], client.cookies["sa_uid"])
        (Path(settings.data_dir) / "uploads" / record.location).write_bytes(b"bad")
        assert client.get(upload["content_url"]).status_code == 422
