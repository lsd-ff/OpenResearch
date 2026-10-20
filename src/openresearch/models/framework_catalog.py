from typing import Literal

from pydantic import Field

from openresearch.models.comparison import ComparisonSpec, FrameworkResearchReport
from openresearch.models.runtime import ContractModel


class FrameworkStudy(ContractModel):
    index: int = Field(ge=1, le=4)
    task_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    report: FrameworkResearchReport


class FrameworkCatalog(ContractModel):
    version: Literal["framework.catalog/v1"] = "framework.catalog/v1"
    comparison: ComparisonSpec
    studies: list[FrameworkStudy] = Field(min_length=2, max_length=4)
    scope: str = "来源与适用性分析，不表示已安装依赖、运行框架或得到实验指标。"
