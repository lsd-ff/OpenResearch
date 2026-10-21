import json

from openresearch.models.comparison import FrameworkResearchReport
from openresearch.models.research import ResearchReport
from openresearch.models.runtime import Plan, Task
from openresearch.storage.files import ArtifactStore


def framework_outputs(plan: Plan, task: Task, files: ArtifactStore) -> dict[str, str]:
    if plan.intent_type != "Framework_Comparison":
        return {}
    if task.type == "framework_catalog":
        return {"framework_research_report": files.read(plan.artifacts["framework_catalog"])}
    if task.type == "framework_report" and "comparison_report" in plan.artifacts:
        return {"evaluation_report": files.read(plan.artifacts["comparison_report"])}
    # 此处接收执行器已投影的单分支视图，不能读取另一个分支的私有产物。
    index = task.inputs.get("framework_index")
    if index is None:
        return {}
    if task.type == "research_prepare":
        raw = json.dumps(
            {
                "version": "framework.preparation/v1",
                "workflow_ref": "openresearch:" + plan.id,
                "stage_task_id": task.id,
                "framework_index": index,
                "environment_lock_sha256": plan.artifacts["environment_lock"].sha256,
                "state": "code_and_lock_prepared",
                "installation_verified": False,
                "lifecycle": "recreate_per_execution",
            }
        )
        result = {f"framework_environment_{index}": raw}
        if index in {1, 2}:
            result[("left" if index == 1 else "right") + "_env"] = raw
        return result
    if task.type == "framework_research":
        raw = files.read(plan.artifacts["framework_research_report"])
        report = FrameworkResearchReport.model_validate_json(raw)
        if report.fit is None:
            return {}
        result = {f"framework_fit_report_{index}": raw}
        if index in {1, 2}:
            result[("left" if index == 1 else "right") + "_fit_report"] = raw
        return result
    if task.type == "research_execute":
        execution = ResearchReport.model_validate_json(
            files.read(plan.artifacts["research_report"])
        )
        raw = json.dumps(execution.runs[0].metrics, allow_nan=False)
        result = {f"framework_metrics_{index}": raw}
        if index in {1, 2}:
            result[("left" if index == 1 else "right") + "_metrics"] = raw
        return result
    return {}
