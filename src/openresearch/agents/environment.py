import asyncio

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.models.sources import EnvironmentSpec
from openresearch.research.specifications import resolved_research
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.wheels import WheelSource
from openresearch.storage.files import ArtifactStore


class EnvironmentAgent:
    def __init__(self, settings: Settings) -> None:
        self.source = WheelSource(settings)
        self.blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)

    async def execute(
        self, task: Task, plan: Plan | None = None, files: ArtifactStore | None = None
    ) -> TaskResult:
        if "environment.lock" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to lock dependencies")
        spec = (
            resolved_research(plan, task, files).environment
            if plan is not None and files is not None and "research_spec" in plan.artifacts
            else EnvironmentSpec.model_validate(task.inputs.get("environment", {}))
        )
        lock, wheels = await self.source.fetch(spec)
        for raw in wheels.values():
            await asyncio.to_thread(self.blobs.write, raw)
        return TaskResult(
            result=f"Locked {len(lock.wheels)} wheel dependencies for CPython 3.12 Linux x86_64.",
            outputs={"environment_lock": lock.model_dump_json()},
        )
