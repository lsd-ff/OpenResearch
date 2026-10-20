"""共享目录只包含授权公开的来源分析，执行分支仍使用各自的私有产物。"""

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from openresearch.errors import ContractError
from openresearch.models.comparison import FrameworkResearchReport, FrameworkTarget
from openresearch.models.framework_catalog import FrameworkCatalog
from openresearch.models.runtime import Plan, Task
from openresearch.storage.files import ArtifactStore


def pinned_target(declared: FrameworkTarget, report: FrameworkResearchReport) -> FrameworkTarget:
    if report.name != declared.name or report.error or not report.packages:
        raise ContractError("framework catalog has no successful matching source report")
    resolved = report.target or declared
    if resolved.name != declared.name or not resolved.requirements:
        raise ContractError("framework catalog lacks the resolved package identity")
    pins = {canonicalize_name(item.name): item.version for item in report.packages}
    requests = [Requirement(value) for value in resolved.requirements]
    if len(pins) != len(report.packages) or set(pins) != {
        canonicalize_name(value.name) for value in requests
    }:
        raise ContractError("framework catalog package identity differs from its target")
    for value in [*declared.requirements, *resolved.requirements]:
        request = Requirement(value)
        version = pins.get(canonicalize_name(request.name))
        if version is None or version not in request.specifier:
            raise ContractError("framework catalog conflicts with declared package constraints")
    if declared.import_roots and declared.import_roots != resolved.import_roots:
        raise ContractError("framework catalog cannot replace declared import roots")
    return resolved.model_copy(
        update={"requirements": [f"{name}=={version}" for name, version in pins.items()]}
    )


def catalog_target(plan: Plan, task: Task, files: ArtifactStore) -> FrameworkTarget:
    declared = FrameworkTarget.model_validate(task.inputs["target"])
    if "framework_catalog" not in task.required_artifacts:
        return declared
    catalog = FrameworkCatalog.model_validate_json(files.read(plan.artifacts["framework_catalog"]))
    index = task.inputs.get("framework_index")
    if type(index) is not int or not 1 <= index <= len(catalog.comparison.targets):
        raise ContractError("framework catalog branch index is invalid")
    if catalog.comparison.targets[index - 1] != declared:
        raise ContractError("framework catalog cannot switch the declared branch")
    matches = [study for study in catalog.studies if study.index == index]
    if len(matches) != 1:
        raise ContractError("framework catalog source is absent or ambiguous")
    return pinned_target(declared, matches[0].report)
