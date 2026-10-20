import asyncio

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.research import ResearchSpec
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.models.sources import EnvironmentSpec
from openresearch.research.environment_policy import dependency_policy
from openresearch.research.environment_preparation import EnvironmentPreparation
from openresearch.research.runtime_selection import runtime_python
from openresearch.research.specifications import resolved_research
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.wheels import WheelSource
from openresearch.storage.files import ArtifactStore


class EnvironmentAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.source = WheelSource(settings)
        self.blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)
        self.preparation = EnvironmentPreparation(settings)

    async def execute(
        self, task: Task, plan: Plan | None = None, files: ArtifactStore | None = None
    ) -> TaskResult:
        if "environment.lock" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to lock dependencies")
        has_design = plan is not None and files is not None and "research_spec" in plan.artifacts
        research = (
            resolved_research(plan, task, files)
            if plan is not None and files is not None
            else ResearchSpec.model_validate(task.inputs.get("research", {}))
        )
        spec = (
            research.environment
            if has_design
            else EnvironmentSpec.model_validate(
                task.inputs.get("environment", research.environment.model_dump())
            )
        )
        python = runtime_python(self.settings, research.runtime)
        if research.runtime and "runtime.select" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to select a research runtime")
        if "environment_preparation_report" in task.output_artifacts:
            requests, constraints = (
                dependency_policy(plan, task, files, ResearchSpec(environment=spec))
                if plan is not None and files is not None
                else (tuple(spec.requirements), tuple(spec.requirements))
            )
            self.preparation.source = self.source
            prepared = await self.preparation.run(
                plan.user_intent if plan else task.description,
                list(requests),
                list(constraints),
                research.max_repairs if "environment.repair" in task.contract.allowed_tools else 0,
                initial_spec=spec,
                lock_wheels=True,
                runtime=research.runtime,
                allow_runtime_select="runtime.select" in task.contract.allowed_tools,
            )
            outputs = {"environment_preparation_report": prepared.report.model_dump_json()}
            if not prepared.report.success or prepared.lock is None or prepared.spec is None:
                return TaskResult(
                    success=False,
                    error=prepared.report.attempts[-1].error,
                    result="Environment preparation failed; actual diagnostics preserved.",
                    structured_data=prepared.report.model_dump_json(),
                    outputs=outputs,
                )
            return TaskResult(
                result=f"Locked {len(prepared.lock.wheels)} verified wheel dependencies.",
                structured_data=prepared.report.model_dump_json(),
                outputs={
                    **outputs,
                    "environment_lock": prepared.lock.model_dump_json(),
                    "prepared_environment": prepared.spec.model_dump_json(),
                },
            )
        lock, wheels = await self.source.for_python(python).fetch(spec)
        for raw in wheels.values():
            await asyncio.to_thread(self.blobs.write, raw)
        return TaskResult(
            result=f"Locked {len(lock.wheels)} wheels for CPython {python} Linux x86_64.",
            outputs={"environment_lock": lock.model_dump_json()},
        )
