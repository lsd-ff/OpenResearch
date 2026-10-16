from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Plan, Task
from openresearch.storage.files import ArtifactStore


def resolved_research(plan: Plan, task: Task, files: ArtifactStore) -> ResearchSpec:
    artifact = plan.artifacts.get("research_spec")
    if artifact is not None:
        return ResearchSpec.model_validate_json(files.read(artifact))
    return ResearchSpec.model_validate(task.inputs.get("research", {}))
