from pydantic import Field

from openresearch.models.runtime import ContractModel


class SourceCitation(ContractModel):
    upload_id: str
    page: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=512)


class ChatReply(ContractModel):
    response: str = Field(min_length=1, max_length=16000)
    citations: list[SourceCitation] = Field(default_factory=list, max_length=16)


class ExecutionSummary(ChatReply):
    """执行汇总引用任务产物；不能填入未提供原文页的 PDF 引用。"""

    citations: list[SourceCitation] = Field(default_factory=list, max_length=0)
