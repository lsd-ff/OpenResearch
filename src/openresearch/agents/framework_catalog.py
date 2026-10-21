from openresearch.errors import ContractError
from openresearch.models.comparison import ComparisonSpec, FrameworkResearchReport
from openresearch.models.framework_catalog import FrameworkCatalog, FrameworkStudy
from openresearch.models.runtime import Plan, Status, Task, TaskResult
from openresearch.research.framework_catalog import pinned_target
from openresearch.storage.files import ArtifactStore


def framework_catalog(plan: Plan, task: Task, files: ArtifactStore) -> TaskResult:
    if "comparison.catalog" not in task.contract.allowed_tools:
        raise ContractError("task is not authorized to aggregate actual framework sources")
    spec = ComparisonSpec.model_validate(task.inputs["comparison"])
    studies = []
    for index, target in enumerate(spec.targets, 1):
        scope = f"framework_{index}"
        candidates = [
            node
            for node in plan.nodes
            if node.type == "framework_research" and node.artifact_scope == scope
        ]
        if len(candidates) != 1 or candidates[0].status != Status.COMPLETED:
            raise ContractError("framework catalog requires every actual source analysis")
        source = candidates[0]
        key = scope + "__framework_research_report"
        artifact = plan.artifacts[key]
        if source.id not in task.dependencies or artifact.producer_task_id != source.id:
            raise ContractError("framework catalog source producer differs from its dependency")
        report = FrameworkResearchReport.model_validate_json(files.read(artifact))
        pinned_target(target, report)
        studies.append(
            FrameworkStudy(
                index=index, task_id=source.id, artifact_sha256=artifact.sha256, report=report
            )
        )
    catalog = FrameworkCatalog(comparison=spec, studies=studies)
    lines = ["# 框架来源与适用性", "", catalog.scope, ""]
    for study in studies:
        lines.append(f"## {study.report.name}")
        lines.extend(
            f"- 实际公开包：{package.name}=={package.version}；"
            f"来源 SHA-256：`{package.metadata_sha256}`。"
            for package in study.report.packages
        )
        if study.report.fit:
            lines.append(study.report.fit.response)
    return TaskResult(
        result="\n\n".join(lines), outputs={"framework_catalog": catalog.model_dump_json()}
    )
