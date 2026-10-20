from typing import Literal

from pydantic import Field

from openresearch.models.runtime import ContractModel
from openresearch.models.runtime_profiles import RuntimeBinding


class GpuDevice(ContractModel):
    name: str = Field(min_length=1, max_length=256)
    memory_mb: int = Field(ge=0)
    free_memory_mb: int = Field(ge=0)


class SandboxResources(ContractModel):
    python: str
    platform: str
    cpu_count: float = Field(ge=0, allow_inf_nan=False)
    memory_mb: int = Field(ge=0)
    workspace_free_mb: int = Field(ge=0)
    workspace_storage: Literal["tmpfs", "volume"] = "tmpfs"
    gpus: list[GpuDevice] = Field(default_factory=list, max_length=16)
    gpu_error: str = Field(default="", max_length=2000)
    container_image_id: str | None = None


class ReproductionRequest(ContractModel):
    mode: Literal["auto", "smoke", "full"] = "auto"
    device: Literal["auto", "cpu", "gpu"] = "auto"


class ReproductionDecision(ContractModel):
    requested_mode: Literal["auto", "smoke", "full"]
    effective_mode: Literal["smoke", "full"]
    full_eligible: bool
    reasons: list[str]
    probe: SandboxResources
    runtime: RuntimeBinding | None = None
    execution_allowed: bool = True
    thresholds: dict[str, float] = Field(default_factory=dict)
