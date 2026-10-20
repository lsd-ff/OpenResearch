from openresearch.models.environment_preparation import EnvironmentPreparationReport
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Plan, Task
from openresearch.models.sources import EnvironmentSpec
from openresearch.storage.files import ArtifactStore


def resolved_research(plan: Plan, task: Task, files: ArtifactStore) -> ResearchSpec:
    artifact = plan.artifacts.get("ablation_research_spec") or plan.artifacts.get("research_spec")
    spec = (
        ResearchSpec.model_validate_json(files.read(artifact))
        if artifact is not None
        else ResearchSpec.model_validate(task.inputs.get("research", {}))
    )
    if prepared := plan.artifacts.get("prepared_environment"):
        # 原研究规范保留来源；实际环境单列，所有后续阶段必须读取同一已校验环境。
        spec = spec.model_copy(
            update={"environment": EnvironmentSpec.model_validate_json(files.read(prepared))}
        )
    for key in ("dependency_resolution_report", "environment_preparation_report"):
        if artifact := plan.artifacts.get(key):
            report = EnvironmentPreparationReport.model_validate_json(files.read(artifact))
            if report.success and report.runtime:
                spec = spec.model_copy(update={"runtime": report.runtime})
    return spec
