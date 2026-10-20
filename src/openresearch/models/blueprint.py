from typing import Literal

from pydantic import Field

from openresearch.models.runtime import ContractModel

TaskKind = Literal[
    "code_generate",
    "code_execute",
    "document_extract",
    "paper_analyze",
    "repository_fetch",
    "environment_lock",
    "claim_freeze",
    "research_design",
    "research_prepare",
    "benchmark_preflight",
    "research_execute",
    "chat_answer",
    "source_discover",
    "data_analyze",
    "ablation_design",
]


class NodeBlueprint(ContractModel):
    ref: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    type: TaskKind
    template_task_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=160)
    assigned_to: Literal["chat_agent", "librarian_agent", "data_agent", "coder_agent"]
    description: str = Field(min_length=1, max_length=4000)
    dependencies: list[str] = Field(default_factory=list, max_length=64)
    priority: int = Field(default=0, ge=-100, le=100)
    parallelizable: bool = True
    query: str | None = Field(default=None, min_length=1, max_length=300)
    search_kind: Literal["paper", "repository"] = "paper"
    upload_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class PlanBlueprint(ContractModel):
    strategy: str = Field(min_length=1, max_length=1000)
    nodes: list[NodeBlueprint] = Field(min_length=1, max_length=64)
