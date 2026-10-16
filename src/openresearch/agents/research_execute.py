import asyncio
import hashlib
from dataclasses import dataclass, replace

from openresearch.agents.claim_assess import ClaimAssessor, verification_markdown
from openresearch.agents.research_prepare import ResearchPreparer
from openresearch.config import Settings
from openresearch.models.execution import ExecutionLease
from openresearch.models.research import (
    ExperimentVariant,
    ResearchPatch,
    ResearchReport,
    ResearchSpec,
)
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.models.sources import EnvironmentLock, RepositorySnapshot
from openresearch.research.benchmark import Dataset, parse_dataset
from openresearch.research.outcomes import comparison, research_result
from openresearch.research.patches import apply_patch
from openresearch.research.repairs import RepairPipeline
from openresearch.research.runner import ExperimentInput, ExperimentRunner
from openresearch.research.specifications import resolved_research
from openresearch.sandbox.base import Sandbox
from openresearch.sandbox.session import SandboxSessions
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository
from openresearch.storage.upload_files import UploadFiles


@dataclass(frozen=True)
class PreparedExperiment:
    snapshot: RepositorySnapshot | None
    original: dict[str, bytes]
    request: ExperimentInput


class ResearchExecutor:
    def __init__(
        self, settings: Settings, repository: Repository, files: ArtifactStore, sandbox: Sandbox
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.files = files
        self.uploads = UploadFiles(settings.data_dir / "uploads", settings.upload_max_bytes)
        self.preparer = ResearchPreparer(settings, files)
        self.runner = ExperimentRunner(settings, SandboxSessions(repository, sandbox))
        self.repairs = RepairPipeline(self.preparer, self.runner)
        self.claims = ClaimAssessor(settings, files)

    def _dataset(self, plan: Plan, spec: ResearchSpec) -> Dataset | None:
        if spec.benchmark is None:
            return None
        source = self.repository.uploads.get(spec.benchmark.upload_id, plan.owner_id)
        return parse_dataset(
            self.uploads.read(source), spec.benchmark, self.settings.benchmark_max_rows
        )

    def _prepare(self, plan: Plan, task: Task, lease: ExecutionLease) -> PreparedExperiment:
        spec = resolved_research(plan, task, self.files)
        snapshot, sources = self.preparer.workspace(plan)
        patch = ResearchPatch.model_validate_json(self.files.read(plan.artifacts["research_patch"]))
        request = ExperimentInput(
            lease=lease,
            spec=spec,
            variant=ExperimentVariant(name="baseline", arguments=spec.arguments),
            files=apply_patch(sources, patch, spec),
            patch=patch,
            lock=EnvironmentLock.model_validate_json(
                self.files.read(plan.artifacts["environment_lock"])
            ),
            dataset=self._dataset(plan, spec),
            number=1,
        )
        return PreparedExperiment(snapshot, sources, request)

    async def execute(self, plan: Plan, task: Task, lease: ExecutionLease) -> TaskResult:
        prepared = await asyncio.to_thread(self._prepare, plan, task, lease)
        baseline = await self.repairs.run(plan.user_intent, prepared.original, prepared.request)
        request = baseline.request
        predictions = {"baseline": baseline.output.predictions}
        runs = [baseline.output.run]
        if not runs[0].attempts[-1].error:
            for variant in request.spec.ablations:
                output = await self.runner.run(replace(request, variant=variant, number=1))
                runs.append(output.run)
                predictions[variant.name] = output.predictions
        report = ResearchReport(
            repository_commit=prepared.snapshot.commit if prepared.snapshot else None,
            runs=runs,
            success=all(not run.attempts[-1].error for run in runs),
            comparison=comparison(runs),
            dataset_upload_id=request.spec.benchmark.upload_id if request.spec.benchmark else None,
            dataset_sha256=request.dataset.sha256 if request.dataset else None,
            environment_lock_sha256=hashlib.sha256(
                request.lock.model_dump_json().encode()
            ).hexdigest(),
            entrypoint=request.spec.entrypoint,
        )
        result = research_result(report, request.patch, predictions)
        graph = await self.claims.assess(plan, report, result.outputs)
        result.outputs["claim_evidence_graph"] = graph.model_dump_json()
        result.outputs["claim_verification_report"] = verification_markdown(graph)
        result.structured_data = graph.model_dump_json()
        return result
