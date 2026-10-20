from openresearch.errors import ContractError
from openresearch.models.comparison import FrameworkSource
from openresearch.models.planning import ExperimentDesign
from openresearch.models.research import BenchmarkPreflightReport, ResearchSpec
from openresearch.models.runtime import Plan, Task
from openresearch.models.sources import EnvironmentLock
from openresearch.storage.files import ArtifactStore


def dependency_policy(
    plan: Plan, task: Task, files: ArtifactStore, spec: ResearchSpec
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    supplied = ResearchSpec.model_validate(task.inputs.get("research", {}))
    constraints = list(supplied.environment.requirements)
    design = plan.artifacts.get("experiment_design")
    requests = (
        ExperimentDesign.model_validate_json(files.read(design)).dependencies
        if design
        else spec.environment.requirements
    )
    if "benchmark_preflight_report" in task.required_artifacts:
        checked = BenchmarkPreflightReport.model_validate_json(
            files.read(plan.artifacts["benchmark_preflight_report"])
        )
        if checked.environment_lock is not None and checked.dependency_requests:
            requests = checked.dependency_requests
    if source := plan.artifacts.get("framework_source"):
        # 框架 API 引用绑定固定版本，环境修复不能悄悄升级主框架使来源失效。
        constraints.extend(
            f"{package.name}=={package.version}"
            for package in FrameworkSource.model_validate_json(files.read(source)).packages
        )
    return tuple(requests), tuple(constraints)


def prepared_lock(plan: Plan, task: Task, files: ArtifactStore) -> EnvironmentLock:
    lock = EnvironmentLock.model_validate_json(files.read(plan.artifacts["environment_lock"]))
    if "benchmark_preflight_report" in task.required_artifacts:
        report = BenchmarkPreflightReport.model_validate_json(
            files.read(plan.artifacts["benchmark_preflight_report"])
        )
        if report.environment_lock is not None:
            if "environment.repair" not in task.contract.allowed_tools:
                raise ContractError("task cannot adopt a recovered preflight environment")
            lock = report.environment_lock
    return lock
