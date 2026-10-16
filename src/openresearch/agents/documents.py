import asyncio
import json
import re
from functools import cache
from importlib import resources
from pathlib import Path

import pypdf

from openresearch.config import Settings
from openresearch.errors import ContractError, SandboxError
from openresearch.models.documents import PaperCorpus, SourceIndex, SourceLink
from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.session import SandboxSessions
from openresearch.storage.repository import Repository
from openresearch.storage.upload_files import UploadFiles


@cache
def parser_library() -> dict[str, bytes]:
    """传输已锁定的纯 Python 解析库，无需容器联网安装依赖。"""
    package = Path(pypdf.__file__).parent
    return {
        f"vendor/pypdf/{path.relative_to(package).as_posix()}": path.read_bytes()
        for path in package.rglob("*.py")
    }


def source_index(corpus: PaperCorpus) -> SourceIndex:
    links: list[SourceLink] = []
    for document in corpus.documents:
        for page in document.pages:
            urls = re.findall(r"https?://[^\s<>\"']+", page.text)
            for url in dict.fromkeys(urls):
                links.append(
                    SourceLink(
                        upload_id=document.source.id, page=page.number, url=url.rstrip(".,;:)")
                    )
                )
    return SourceIndex(links=links[:1000])


class DocumentAgent:
    def __init__(self, settings: Settings, repository: Repository, sandbox: Sandbox) -> None:
        self.settings = settings
        self.repository = repository
        self.uploads = UploadFiles(settings.data_dir / "uploads", settings.upload_max_bytes)
        self.sessions = SandboxSessions(repository, sandbox)

    async def _inputs(self, plan: Plan) -> dict[str, bytes]:
        sources = [
            self.repository.uploads.get(identifier, plan.owner_id)
            for identifier in plan.attachments
        ]
        if sum(source.size for source in sources) > self.settings.upload_max_bytes:
            raise ContractError("combined document inputs exceed configured size limit")
        limits = {
            "max_pages": self.settings.document_max_pages,
            "max_text_bytes": self.settings.document_max_text_bytes,
            "max_result_bytes": self.settings.max_output_bytes * 3,
        }
        files = await asyncio.to_thread(parser_library)
        # 库缓存只读，建立传输映射后补入该任务的数据，避免污染后续任务。
        inputs = dict(files)
        inputs["document_input.json"] = json.dumps(
            {"sources": [source.source().model_dump() for source in sources], "limits": limits}
        ).encode()
        for source in sources:
            inputs[f"inputs/{source.id}"] = await asyncio.to_thread(self.uploads.read, source)
        return inputs

    async def execute(self, plan: Plan, task: Task, lease: ExecutionLease) -> TaskResult:
        if "documents.extract" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to extract documents")
        program = (
            resources.files("openresearch.sandbox.programs")
            .joinpath("pdf_extract.py")
            .read_text(encoding="utf-8")
        )
        code = "import sys\nsys.path.insert(0, '/tmp/openresearch/vendor')\n" + program
        async with self.sessions.open(lease) as session:
            await session.put_files(await self._inputs(plan))
            result = await session.execute(code)
            if result.exit_code != 0:
                raise SandboxError(f"document extraction failed: {result.stderr[-2000:]}")
            raw = await session.get_file("parsed_paper.json", self.settings.max_output_bytes * 3)
        corpus = PaperCorpus.model_validate_json(raw)
        if len(corpus.documents) != len(plan.attachments) or {
            document.source.id for document in corpus.documents
        } != set(plan.attachments):
            raise ContractError("document extraction returned unexpected source identities")
        for document in corpus.documents:
            expected = self.repository.uploads.get(document.source.id, plan.owner_id).source()
            if document.source != expected:
                raise ContractError("document extraction changed its source identity or checksum")
        return TaskResult(
            result=result.stdout[:4096],
            outputs={
                "parsed_paper": corpus.model_dump_json(),
                "source_index": source_index(corpus).model_dump_json(),
            },
        )
