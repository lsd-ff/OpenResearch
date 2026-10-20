from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openresearch.models.graph import validate_graph


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid4().hex


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Status(StrEnum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELED = "canceled"
    AWAITING_APPROVAL = "awaiting_approval"


class TaskContract(ContractModel):
    version: str = "v1"
    input_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)


class Task(ContractModel):
    id: str = Field(default_factory=new_id)
    name: str
    type: str
    description: str = ""
    assigned_to: str
    artifact_scope: str | None = Field(default=None, pattern=r"^framework_[1-9][0-9]*$")
    trigger_rule: Literal["all_success", "all_done"] = "all_success"
    status: Status = Status.PENDING
    dependencies: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)
    parallelizable: bool = True
    priority: int = 0
    retry_limit: int = Field(default=0, ge=0, le=3)
    run_count: int = 0
    execution_id: str | None = None
    execution_epoch: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    contract: TaskContract = Field(default_factory=TaskContract)
    inputs: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    code: str = ""
    structured_data: str = ""
    image_base64: str = Field(default="", max_length=2800000)
    error: str = ""


class Edge(ContractModel):
    id: str = Field(default_factory=new_id)
    from_: str = Field(alias="from")
    to: str
    type: str = "control"


class Approval(ContractModel):
    required: bool = False
    status: str = "not_required"
    approved_by: str | None = None
    approved_at: datetime | None = None


class Budget(ContractModel):
    max_task_attempts: int = Field(default=10, ge=1)
    max_duration_seconds: int = Field(default=600, ge=1)


class Usage(ContractModel):
    task_attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Artifact(ContractModel):
    id: str = Field(default_factory=new_id)
    key: str
    type: str
    producer_task_id: str
    execution_id: str
    value: str = ""  # 前端只取有限长度预览，完整内容从产物文件读取。
    location: str  # 相对产物根目录的路径，禁止直接读取任意宿主文件。
    sha256: str
    size: int
    created_at: datetime = Field(default_factory=utcnow)


class PlanningInfo(ContractModel):
    strategy: Literal["template", "llm"] = "template"
    reason: str = "explicit_workflow"


class Plan(ContractModel):
    id: str = Field(default_factory=new_id)
    user_intent: str
    intent_type: str = "Code_Execution"
    owner_id: str
    session_id: str
    attachments: list[str] = Field(default_factory=list, max_length=8)
    trace_id: str = Field(default_factory=new_id)
    planning: PlanningInfo = Field(default_factory=PlanningInfo)
    status: Status = Status.PENDING
    approval: Approval = Field(default_factory=Approval)
    budget: Budget = Field(default_factory=Budget)
    usage: Usage = Field(default_factory=Usage)
    nodes: list[Task]
    edges: list[Edge] = Field(default_factory=list)
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    execution_scope: list[str] | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def validate_graph(self) -> "Plan":
        validate_graph(self)
        return self

    @property
    def meta(self) -> dict[str, int]:
        return {
            "total_nodes": len(self.nodes),
            **{
                f"{status}_nodes": sum(node.status == status for node in self.nodes)
                for status in ("completed", "failed", "blocked", "in_progress", "ready")
            },
        }


class Event(ContractModel):
    sequence: int = 0
    plan_id: str
    event_type: str
    task_id: str | None = None
    task_status: Status | None = None
    execution_id: str | None = None
    trace_id: str = ""
    span_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)


class ExecutionResult(ContractModel):
    stdout: str = ""
    stderr: str = ""
    exit_code: int


class TaskResult(ContractModel):
    success: bool = True
    error: str = ""
    result: str = ""
    code: str = ""
    structured_data: str = ""
    image_base64: str = Field(default="", max_length=2800000)
    outputs: dict[str, str] = Field(default_factory=dict)
