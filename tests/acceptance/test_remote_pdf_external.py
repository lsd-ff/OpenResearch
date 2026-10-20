import hashlib
import io
import os

import pytest
from test_research import finish

from openresearch.models.context import Identity
from openresearch.models.documents import PaperCorpus
from openresearch.models.runtime import Status
from openresearch.models.submissions import PlanSubmission
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker
from openresearch.services.plans import PlanService
from openresearch.services.uploads import UploadService
from openresearch.sources.remote_pdf import RemotePdfReader
from openresearch.storage.upload_files import UploadSubmission

URL = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="public PDF opt-in required"
    ),
]


@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_actual_public_pdf_download_supports_http_redirect_and_verified_https(
    settings, scheme
):
    raw = await RemotePdfReader(settings).fetch(URL.replace("https:", scheme + ":", 1))
    assert raw.startswith(b"%PDF-") and 100 < len(raw) <= settings.upload_max_bytes


@pytest.mark.docker
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_DOCKER") != "1", reason="Docker opt-in required")
async def test_actual_public_pdf_keeps_download_hash_and_page_text_after_sandbox_extraction(
    settings, repository, files
):
    raw = await RemotePdfReader(settings).fetch(URL)
    identity = Identity("public-pdf", "extract")
    record = UploadService(settings, repository).create(
        UploadSubmission("downloaded.pdf", io.BytesIO(raw), identity)
    )
    plan = PlanService(settings, repository).create(
        PlanSubmission(
            "Extract the actual downloaded PDF", identity, attachments=[record.id], workflow="paper"
        )
    )
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        final = repository.get_plan(plan.id)
        assert final.status == Status.COMPLETED, [node.error for node in final.nodes]
        corpus = PaperCorpus.model_validate_json(files.read(final.artifacts["parsed_paper"]))
        document = corpus.documents[0]
        assert document.source.sha256 == hashlib.sha256(raw).hexdigest()
        assert document.source.id == record.id and len(document.pages) == 1
        assert document.pages[0].text.strip() == "Dummy PDF file"
        assert not document.pages[0].needs_ocr
        assert not repository.cleanup_candidates("docker")
    finally:
        await worker._shutdown()
