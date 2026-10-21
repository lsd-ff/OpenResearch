from pydantic import Field

from openresearch.models.environment_actions import (
    EnvironmentRepairProposal as EnvironmentRepairProposal,
)
from openresearch.models.environment_preparation import EnvironmentPreparationReport
from openresearch.models.runtime import ContractModel
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.models.sources import EnvironmentLock


class EnvironmentRepairDecision(ContractModel):
    before_sha256: str
    after_sha256: str | None = None
    proposal: EnvironmentRepairProposal | None = None
    accepted: bool = False
    error: str = ""
    environment_lock: EnvironmentLock | None = None
    runtime_before: RuntimeBinding | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    runtime_after: RuntimeBinding | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    preparation: EnvironmentPreparationReport | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    repair_cost: int = Field(default=1, ge=1, le=3, exclude_if=lambda value: value == 1)
