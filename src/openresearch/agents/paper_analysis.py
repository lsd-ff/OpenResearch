import json

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.llm.client import LLMClient
from openresearch.models.chat import ChatReply
from openresearch.models.documents import PaperCorpus
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.research.source_context import document_context
from openresearch.services.chat_context import CHAT_PROMPT
from openresearch.storage.files import ArtifactStore


class PaperAnalyst:
    def __init__(self, settings: Settings, files: ArtifactStore) -> None:
        self.files = files
        self.llm = LLMClient(settings)

    async def execute(self, plan: Plan, task: Task) -> TaskResult:
        if "documents.answer" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to analyze documents")
        pages = json.loads(document_context(plan, self.files))
        reply = await self.llm.request_typed(
            [
                {"role": "system", "content": CHAT_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"request": task.description, "pages": pages}, ensure_ascii=False
                    ),
                },
            ],
            ChatReply,
        )
        allowed = {(page["upload_id"], page["page"]): page["text"] for page in pages}
        corpus = PaperCorpus.model_validate_json(self.files.read(plan.artifacts["parsed_paper"]))
        original = {
            (document.source.id, page.number): page.text
            for document in corpus.documents
            for page in document.pages
        }
        for citation in reply.citations:
            key = citation.upload_id, citation.page
            if (
                not citation.quote.strip()
                or citation.quote not in allowed.get(key, "")
                or citation.quote not in original.get(key, "")
            ):
                raise ContractError("analysis citation does not match the supplied source page")
        return TaskResult(
            result=reply.response, outputs={"paper_analysis": reply.model_dump_json()}
        )
