import asyncio
import hashlib
from dataclasses import dataclass, replace

from openresearch.agents.claim_assess import ClaimAssessor, verification_markdown
from openresearch.agents.research_prepare import ResearchPreparer
from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.ablation import AblationPlan
from openresearch.models.comparison import FrameworkSource
from openresearch.models.environment_preparation import EnvironmentPreparationReport
from openresearch.models.execution import ExecutionLease
from openresearch.models.research import (
    BenchmarkPreflightReport,
    ExperimentVariant,
    ResearchPatch,
    ResearchReport,
    ResearchSpec,
    SourceIntegrity,
)
from openresearch.models.runtime import Plan, Task, TaskResult
from openresearch.models.sources import RepositorySnapshot
from openresearch.research.benchmark import Dataset, parse_dataset
from openresearch.research.environment_policy import dependency_policy, prepared_lock
from openresearch.research.environment_repair import ResearchEnvironmentRepairer
from openresearch.research.interpreter import interpreter_probe
from openresearch.research.outcomes import comparison, research_result
from openresearch.research.patches import apply_patch
from openresearch.research.preflight import BenchmarkPreflight, verify_preflight
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


def provided_source_identity(
    plan: Plan, task: Task, request: ExperimentInput
) -> SourceIntegrity | None:
    code = next(
        (
            node.inputs.get("code")
            for node in plan.nodes
            if node.type == "research_prepare" and node.artifact_scope == task.artifact_scope
        ),
        None,
    )
    entrypoint = request.files.get(request.spec.entrypoint)
    if not isinstance(code, str) or entrypoint is None:
        return None
    return SourceIntegrity(
        provided_code_sha256=hashlib.sha256(code.encode()).hexdigest(),
        prepared_entrypoint_sha256=hashlib.sha256(entrypoint).hexdigest(),
    )


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
        self.repairs = RepairPipeline(
            self.preparer, self.runner, ResearchEnvironmentRepairer(settings)
        )
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
        if spec.runtime and "runtime.select" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to select a research runtime")
        if spec.reproduction and "runtime.probe" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to admit compute resources")
        snapshot, sources = self.preparer.workspace(plan)
        patch_key = (
            "final_patch"
            if task.type == "research_gap_repair"
            else (
                "benchmark_preflight_patch"
                if "benchmark_preflight_patch" in task.required_artifacts
                else "research_patch"
            )
        )
        patch = ResearchPatch.model_validate_json(self.files.read(plan.artifacts[patch_key]))
        source = plan.artifacts.get("framework_source")
        source_raw = self.files.read(source) if source else ""
        modules = FrameworkSource.model_validate_json(source_raw).import_roots if source else []
        requests, constraints = dependency_policy(plan, task, self.files, spec)
        request = ExperimentInput(
            lease=lease,
            spec=spec,
            variant=ExperimentVariant(name="baseline", arguments=spec.arguments),
            files=apply_patch(sources, patch, spec),
            patch=patch,
            lock=prepared_lock(plan, task, self.files),
            dataset=self._dataset(plan, spec),
            number=1,
            required_modules=tuple(modules),
            framework_source_sha256=hashlib.sha256(
                source_raw.encode() + sources.get("openresearch_comparison.py", b"")
            ).hexdigest()
            if source
            else None,
            dependency_requests=requests,
            dependency_constraints=constraints,
            allow_environment_repair="environment.repair" in task.contract.allowed_tools,
            allow_patch_repair="research.patch" in task.contract.allowed_tools,
            # 原运行也不能通过删除断言、测试或实际库来制造成功；与差距修复共用校验。
            preserve_checks=True,
            # 业务 RuntimeError 可被实际重测纠正；断言、测试函数和退出信号仍不可删除。
            preserve_raises=False,
            allow_runtime_select="runtime.select" in task.contract.allowed_tools,
            runtime_fixed=ResearchSpec.model_validate(task.inputs.get("research", {})).runtime
            is not None,
            interpreter_probe=interpreter_probe(
                self.settings, self.runner.sessions, lease, task, plan.user_intent
            ),
        )
        return PreparedExperiment(snapshot, sources, request)

    async def preflight(self, plan: Plan, task: Task, lease: ExecutionLease) -> TaskResult:
        if "benchmark.preflight" not in task.contract.allowed_tools:
            raise ContractError("task is not authorized to preflight benchmark code")
        prepared = await asyncio.to_thread(self._prepare, plan, task, lease)
        report = await BenchmarkPreflight(self.repairs).run(
            plan.user_intent,
            prepared.original,
            prepared.request,
            allow_repairs=bool(
                {"research.patch", "environment.repair"}.intersection(task.contract.allowed_tools)
            ),
        )
        final = report.run.attempts[-1]
        return TaskResult(
            success=report.status == "passed",
            error=final.error,
            result=f"Benchmark preflight {report.status}: {len(report.sample_ids)} actual samples.",
            structured_data=report.model_dump_json(),
            outputs={
                "benchmark_preflight_report": report.model_dump_json(),
                "benchmark_preflight_patch": final.patch.model_dump_json(),
            },
        )

    async def execute(self, plan: Plan, task: Task, lease: ExecutionLease) -> TaskResult:
        prepared = await asyncio.to_thread(self._prepare, plan, task, lease)
        preflight = None
        if "benchmark_preflight_report" in task.required_artifacts:
            preflight = BenchmarkPreflightReport.model_validate_json(
                await asyncio.to_thread(
                    self.files.read, plan.artifacts["benchmark_preflight_report"]
                )
            )
            verify_preflight(preflight, prepared.request)

        return await self.execute_prepared(plan, task, prepared, preflight)

    async def execute_prepared(
        self,
        plan: Plan,
        task: Task,
        prepared: PreparedExperiment,
        preflight: BenchmarkPreflightReport | None = None,
        *,
        repair_limit: int | None = None,
    ) -> TaskResult:

        async def recheck(request: ExperimentInput) -> BenchmarkPreflightReport:
            checked = await BenchmarkPreflight(self.repairs).run(
                plan.user_intent, prepared.original, request, allow_repairs=False
            )
            if checked.status == "passed":
                verify_preflight(checked, request)
            return checked

        # 旧计划不自动扩权；缺少重新预检权限时可以执行原冻结程序，但不能修复后绕过门禁。
        allow_repairs = bool(
            {"research.patch", "environment.repair"}.intersection(task.contract.allowed_tools)
        ) and (preflight is None or "benchmark.preflight" in task.contract.allowed_tools)
        baseline = await self.repairs.run(
            plan.user_intent,
            prepared.original,
            prepared.request,
            recheck=recheck if preflight else None,
            allow_repairs=allow_repairs,
            **({"repair_limit": repair_limit} if repair_limit is not None else {}),
        )
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
            runtime=request.spec.runtime,
            reproduction=runs[0].reproduction,
            source_integrity=provided_source_identity(plan, task, request),
            preflight=preflight,
            preflight_rechecks=list(baseline.rechecks),
            environment_changes=list(baseline.environment_changes),
            final_environment_lock=request.lock,
            environment_preparation=[
                EnvironmentPreparationReport.model_validate_json(self.files.read(artifact))
                for key in ("dependency_resolution_report", "environment_preparation_report")
                if (artifact := plan.artifacts.get(key))
            ],
            ablation_plan=AblationPlan.model_validate_json(
                self.files.read(plan.artifacts["ablation_plan"])
            )
            if "ablation_plan" in task.required_artifacts
            else None,
        )
        result = research_result(report, request.patch, predictions)
        graph = await self.claims.assess(plan, report, result.outputs)
        result.outputs["claim_evidence_graph"] = graph.model_dump_json()
        result.outputs["claim_verification_report"] = verification_markdown(graph)
        result.structured_data = graph.model_dump_json()
        return result
