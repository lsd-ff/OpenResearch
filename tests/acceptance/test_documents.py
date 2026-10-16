import asyncio
import io
import json

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from test_docker_smoke import pytestmark as pytestmark

from openresearch.api.app import create_app
from openresearch.config import Settings
from openresearch.models.runtime import Status
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


def real_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 40 740 Td (Research result is 0.75.) Tj "
        b"0 -20 Td (https://github.com/example/research) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_blank_page(width=612, height=792)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


async def wait_for_terminal(worker: Worker, repository: Repository, plan_id: str) -> None:
    async with asyncio.timeout(90):
        while repository.get_plan(plan_id).status not in {Status.COMPLETED, Status.FAILED}:
            await worker.tick()
            await asyncio.sleep(0.05)
        await asyncio.gather(*(active.future for active in worker.active.values()))


async def test_pdf_is_extracted_in_container_with_page_provenance(
    settings: Settings, repository: Repository, files: ArtifactStore
) -> None:
    sandbox = DockerSandbox(settings)
    worker = Worker(settings, repository, files, sandbox)
    owner = {"X-User-Id": "alice", "X-Session-Id": "paper-session"}
    try:
        with TestClient(create_app(settings, repository, sandbox)) as client:
            upload = client.post(
                "/api/uploads",
                headers=owner,
                files={"file": ("paper.pdf", real_pdf(), "application/pdf")},
            ).json()
            response = client.post(
                "/api/plan",
                headers=owner,
                json={
                    "intent": "解析附件论文",
                    "attachments": [upload["id"]],
                    "workflow": "paper",
                },
            )
            assert response.status_code == 200
            plan_id = response.json()["plan_graph"]["id"]
            assert client.post(f"/api/plans/{plan_id}/execute", headers=owner).status_code == 202
            await wait_for_terminal(worker, repository, plan_id)
            finished = repository.get_plan(plan_id)
            assert finished.status == Status.COMPLETED, finished.nodes[0].error
            documents = client.get(f"/api/plans/{plan_id}/documents", headers=owner).json()[
                "documents"
            ]
            assert documents[0]["status"] == "partial_text" and documents[0]["page_count"] == 2
            page = client.get(documents[0]["pages"][0]["content_url"], headers=owner).json()
            assert "Research result is 0.75" in page["text"]
            assert page["source"]["sha256"] == upload["sha256"]
            empty_page = client.get(documents[0]["pages"][1]["content_url"], headers=owner).json()
            assert empty_page["needs_ocr"]
            links = json.loads(files.read(finished.artifacts["source_index"]))["links"]
            assert links[0]["page"] == 1 and links[0]["upload_id"] == upload["id"]
            assert (
                client.get(
                    f"/api/plans/{plan_id}/documents", headers={"X-User-Id": "bob"}
                ).status_code
                == 404
            )
            assert client.get("/api/session", headers=owner).json()["document_plans"] == [plan_id]
    finally:
        await worker._shutdown()


@pytest.mark.parametrize("failure", ["malformed", "page_limit"])
async def test_invalid_or_oversized_pdf_fails_without_success_artifact(
    failure: str, settings: Settings, repository: Repository, files: ArtifactStore
) -> None:
    settings.document_max_pages = 1
    sandbox = DockerSandbox(settings)
    worker = Worker(settings, repository, files, sandbox)
    try:
        with TestClient(create_app(settings, repository, sandbox)) as client:
            raw = b"%PDF-1.4\ninvalid" if failure == "malformed" else real_pdf()
            upload = client.post("/api/uploads", files={"file": ("paper.pdf", raw)}).json()
            plan = client.post(
                "/api/plan",
                json={
                    "intent": "parse",
                    "attachments": [upload["id"]],
                    "workflow": "paper",
                },
            ).json()["plan_graph"]
            client.post(f"/api/plans/{plan['id']}/execute")
            await wait_for_terminal(worker, repository, plan["id"])
            finished = repository.get_plan(plan["id"])
            assert finished.status == Status.FAILED
            assert ("EOF" if failure == "malformed" else "PDF page count") in finished.nodes[
                0
            ].error
            assert "parsed_paper" not in finished.artifacts
            assert repository.cleanup_candidates("docker") == []
    finally:
        await worker._shutdown()
