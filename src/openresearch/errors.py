"""Domain errors translated into explicit API responses."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openresearch.models.planning import PendingPlanning


class NotFoundError(Exception):
    """The requested resource is unavailable to this caller."""


class ConflictError(Exception):
    """The requested transition is invalid for the current state."""


class ContractError(Exception):
    """A plan, tool call, or result violates its declared contract."""


class PlanningInputRequired(ContractError):
    def __init__(self, pending: "PendingPlanning") -> None:
        super().__init__(pending.question)
        self.pending = pending


class StaleExecutionError(ConflictError):
    """An expired or canceled attempt cannot submit a result."""


class SandboxError(Exception):
    """Sandbox creation, execution, or cleanup failed."""


class ModelError(Exception):
    """A model provider returned an invalid or unsuccessful response."""


class UploadTooLargeError(ContractError):
    """上传超过配置的大小上限。"""


class UnsupportedMediaError(ContractError):
    """上传格式或实际文件内容不受支持。"""
