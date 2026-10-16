from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from openresearch.models.runtime import ContractModel, new_id, utcnow


class SourceDocument(ContractModel):
    id: str
    name: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content_type: str


class UploadRecord(SourceDocument):
    id: str = Field(default_factory=new_id)
    owner_id: str
    session_id: str
    size: int = Field(ge=1)
    location: str
    created_at: datetime = Field(default_factory=utcnow)

    def source(self) -> SourceDocument:
        return SourceDocument(
            id=self.id, name=self.name, sha256=self.sha256, content_type=self.content_type
        )


class ParsedPage(ContractModel):
    number: int = Field(ge=1)
    text: str = Field(max_length=500000)
    needs_ocr: bool = False

    @model_validator(mode="after")
    def classify_text(self) -> "ParsedPage":
        self.needs_ocr = not self.text.strip()
        return self


class ParsedDocument(ContractModel):
    source: SourceDocument
    title: str = ""
    pages: list[ParsedPage] = Field(min_length=1, max_length=300)
    status: Literal["parsed", "partial_text", "requires_ocr"] = "parsed"

    @model_validator(mode="after")
    def validate_pages(self) -> "ParsedDocument":
        if [page.number for page in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("page numbers must be contiguous and start at one")
        empty = sum(page.needs_ocr for page in self.pages)
        self.status = (
            "requires_ocr" if empty == len(self.pages) else ("partial_text" if empty else "parsed")
        )
        return self


class PaperCorpus(ContractModel):
    version: str = "paper.corpus/v1"
    documents: list[ParsedDocument] = Field(min_length=1, max_length=8)


class SourceLink(ContractModel):
    upload_id: str
    page: int = Field(ge=1)
    url: str


class SourceIndex(ContractModel):
    version: str = "paper.sources/v1"
    links: list[SourceLink] = Field(default_factory=list)
