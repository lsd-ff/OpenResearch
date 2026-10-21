from openresearch.config import Settings
from openresearch.models.compute import ReproductionRequest
from openresearch.models.runtime import Task, TaskContract
from openresearch.models.runtime_profiles import RuntimeBinding


def compute_task(
    settings: Settings, request: ReproductionRequest, runtime: RuntimeBinding | None
) -> Task:
    tools = ["runtime.probe", *(["runtime.select"] if runtime else [])]
    return Task(
        name="实际资源与复现模式检查",
        type="runtime_prepare",
        assigned_to="coder_agent",
        inputs={
            "reproduction": request.model_dump(),
            "runtime": runtime.model_dump() if runtime else None,
        },
        output_artifacts=["reproduction_decision"],
        timeout_seconds=settings.task_timeout_seconds,
        contract=TaskContract(allowed_tools=tools, output_artifacts=["reproduction_decision"]),
    )
