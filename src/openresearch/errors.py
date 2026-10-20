"""Domain errors translated into explicit API responses."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openresearch.models.planning import PendingPlanning
    from openresearch.models.runtime import ExecutionResult


class NotFoundError(Exception):
    """The requested resource is unavailable to this caller."""


class ConflictError(Exception):
    """The requested transition is invalid for the current state."""


class ContractError(Exception):
    """A plan, tool call, or result violates its declared contract."""


class DependencyResolutionError(ContractError):
    def __init__(self, diagnostic: str, exit_code: int) -> None:
        self.diagnostic, self.exit_code = diagnostic[-16000:], exit_code
        super().__init__("dependency resolution failed: " + self.diagnostic[-2000:])


class EnvironmentLockError(ContractError):
    """公开包版本、平台或依赖闭包不兼容；完整性/预算错误不属于可修复类别。"""


class PlanningInputRequired(ContractError):
    def __init__(self, pending: "PendingPlanning") -> None:
        super().__init__(pending.question)
        self.pending = pending


class StaleExecutionError(ConflictError):
    """An expired or canceled attempt cannot submit a result."""


class SandboxError(Exception):
    """Sandbox creation, execution, or cleanup failed."""


class ResourceBusyError(SandboxError):
    """GPU 已被真实容器占用；不得当成后端故障回退到未受治理的服务。"""


class DependencyInstallError(SandboxError):
    def __init__(self, result: "ExecutionResult") -> None:
        self.result = result
        super().__init__(f"offline dependency installation failed: {result.stderr[-2000:]}")


class ModelError(Exception):
    """A model provider returned an invalid or unsuccessful response."""


class UploadTooLargeError(ContractError):
    """上传超过配置的大小上限。"""


class UnsupportedMediaError(ContractError):
    """上传格式或实际文件内容不受支持。"""
