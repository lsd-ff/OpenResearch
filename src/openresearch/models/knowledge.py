from typing import Annotated, Literal

from pydantic import Field

from openresearch.models.chat import ChatReply, SourceCitation
from openresearch.models.discovery import SearchResult
from openresearch.models.documents import SourceDocument
from openresearch.models.runtime import ContractModel

KNOWLEDGE_ROLES = {
    "general_research": {"librarian_agent"},
    "general_synthesis": {"data_agent"},
    "general_process": {"general_agent", "coder_agent", "chat_agent"},
}
KNOWLEDGE_TYPES = {
    "general_research": "knowledge_research",
    "general_synthesis": "knowledge_synthesis",
    "general_process": "knowledge_process",
}


class KnowledgeProposal(ChatReply):
    response: str = Field(min_length=1, max_length=8000)
    source_ids: list[Annotated[int, Field(ge=0, le=9)]] = Field(default_factory=list, max_length=10)


class KnowledgeEvidence(ContractModel):
    source_id: int = Field(ge=0, le=9)
    title: str
    url: str
    quote: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class KnowledgeReport(ContractModel):
    version: Literal["knowledge.report/v1"] = "knowledge.report/v1"
    response: str = Field(min_length=1, max_length=16000)
    sources: list[SearchResult] = Field(default_factory=list, max_length=10)
    documents: list[SourceDocument] = Field(default_factory=list, max_length=8)
    citations: list[SourceCitation] = Field(default_factory=list, max_length=16)
    evidence: list[KnowledgeEvidence] = Field(default_factory=list, max_length=10)
    input_sha256: dict[str, str] = Field(default_factory=dict, max_length=8)
    scope: str


class KnowledgeValidationAttempt(ContractModel):
    proposal: KnowledgeProposal | None = None
    accepted: bool = False
    error: str = Field(default="", max_length=2000)


class KnowledgeValidationReport(ContractModel):
    version: Literal["knowledge.validation/v1"] = "knowledge.validation/v1"
    input_sha256: dict[str, str] = Field(default_factory=dict, max_length=8)
    attempts: list[KnowledgeValidationAttempt] = Field(default_factory=list, max_length=2)
