from typing import Literal

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from pydantic import Field, field_validator, model_validator

from openresearch.models.runtime import ContractModel


class EnvironmentRepairProposal(ContractModel):
    action: Literal["dependencies", "stop"]
    reason: str = Field(min_length=1, max_length=2000)
    requirements: list[str] = Field(default_factory=list, max_length=8)
    remove_requirements: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("remove_requirements")
    @classmethod
    def package_names_only(cls, values: list[str]) -> list[str]:
        names: list[str] = []
        for value in values:
            requirement = Requirement(value)
            if requirement.url or requirement.extras or requirement.marker or requirement.specifier:
                raise ValueError("removed dependencies must be bare public package names")
            names.append(canonicalize_name(requirement.name))
        if len(set(names)) != len(names):
            raise ValueError("duplicate removed dependencies")
        return names

    @model_validator(mode="after")
    def bounded_changes(self) -> "EnvironmentRepairProposal":
        # 删除、替换与新增共用同一变更预算，不能用多个动作类别扩大一次修复的范围。
        if len(self.requirements) + len(self.remove_requirements) > 8:
            raise ValueError("environment recovery exceeds eight total dependency changes")
        return self
