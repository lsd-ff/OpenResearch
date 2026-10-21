import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from openresearch.errors import SandboxError
from openresearch.sandbox.core.models import CommandResult


@dataclass(frozen=True)
class CommandEvidence:
    exit_code: int | None
    timed_out: bool
    output_limited: bool
    audit_path: Path
    summary: str

    @classmethod
    def from_result(cls, result: CommandResult) -> "CommandEvidence":
        return cls(
            result.exit_code,
            result.timed_out,
            result.output_limited,
            result.audit_path,
            result.summary[:4000],
        )


@dataclass
class AttemptReceipt:
    id: str
    candidate_index: int
    round: int
    base_version: int
    command_sha256: str
    audit_directory: Path
    status: Literal[
        "waiting",
        "running",
        "succeeded",
        "failed",
        "canceled",
        "discarded",
        "superseded",
        "published",
    ] = "waiting"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    container_id: str | None = None
    snapshot_image: str | None = None
    result: CommandEvidence | None = None
    error_type: str | None = None
    gpu_reservation_id: str | None = None


@dataclass
class NodeReceipt:
    id: str
    status: Literal[
        "pending", "running", "completed", "failed", "timed_out", "canceled", "blocked"
    ] = "pending"
    started_at: float | None = None
    finished_at: float | None = None
    rounds: int = 0
    published_version: int | None = None
    selected_candidate: int | None = None
    error_type: str | None = None
    attempts: list[AttemptReceipt] = field(default_factory=list)
    io_weight: int = 1
    gpu_weight: int = 0


@dataclass
class GraphReceipt:
    id: str
    nodes: list[NodeReceipt]
    audit_path: Path
    status: Literal["running", "completed", "failed", "timed_out", "canceled"] = "running"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    version: int = 0
    error_type: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"schema": "openresearch.core-executor/v1", **asdict(self)}


class ExecutionError(SandboxError):
    def __init__(self, receipt: GraphReceipt) -> None:
        super().__init__("core graph execution failed; inspect its factual receipt")
        self.receipt = receipt


class NodeExecutionError(SandboxError):
    pass


class AttemptBudgetExceeded(SandboxError):
    pass
