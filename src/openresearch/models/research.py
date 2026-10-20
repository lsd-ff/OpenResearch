from typing import Literal

from pydantic import Field, field_validator, model_validator

from openresearch.models.ablation import AblationBudget, AblationPlan
from openresearch.models.claims import RubricDraft
from openresearch.models.compute import ReproductionDecision, ReproductionRequest
from openresearch.models.environment_preparation import EnvironmentPreparationReport
from openresearch.models.environment_repair import EnvironmentRepairDecision
from openresearch.models.runtime import ContractModel
from openresearch.models.runtime_profiles import RuntimeBinding
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec, RepositorySpec
from openresearch.sandbox.file_transfer import relative_path


class BenchmarkSpec(ContractModel):
    upload_id: str
    format: Literal["csv", "tsv", "jsonl", "json"] = "csv"
    input_columns: list[str] = Field(min_length=1, max_length=16)
    target_column: str
    id_column: str | None = None
    task: Literal["classification", "regression"] = "classification"
    metrics: list[Literal["accuracy", "macro_f1", "mse", "mae", "r2"]] = Field(
        default=["accuracy"], min_length=1, max_length=3
    )

    @model_validator(mode="after")
    def validate_mapping(self) -> "BenchmarkSpec":
        if self.id_column == self.target_column:
            raise ValueError("sample identifiers must not expose target labels")
        if self.target_column in self.input_columns or self.id_column in self.input_columns:
            raise ValueError("target and identifier must be excluded from predictor inputs")
        if len(set(self.input_columns)) != len(self.input_columns):
            raise ValueError("duplicate predictor input columns")
        allowed = (
            {"accuracy", "macro_f1"} if self.task == "classification" else {"mse", "mae", "r2"}
        )
        if set(self.metrics) - allowed or len(set(self.metrics)) != len(self.metrics):
            raise ValueError("metrics do not match the benchmark task")
        return self


class ExperimentVariant(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    arguments: list[str] = Field(default_factory=list, max_length=16)


class ResearchSpec(ContractModel):
    repository: RepositorySpec | None = None
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    # 缺省字段不进入旧规范的序列化，保持已冻结预检的摘要可回放。
    runtime: RuntimeBinding | None = Field(default=None, exclude_if=lambda value: value is None)
    reproduction: ReproductionRequest | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    entrypoint: str = "openresearch_run.py"
    arguments: list[str] = Field(default_factory=list, max_length=16)
    patch_paths: list[str] = Field(default_factory=lambda: ["openresearch_run.py"], max_length=3)
    test_paths: list[str] = Field(default_factory=list, max_length=8)
    max_repairs: int = Field(default=1, ge=0, le=2)
    benchmark: BenchmarkSpec | None = None
    ablations: list[ExperimentVariant] = Field(default_factory=list, max_length=6)
    ablation_search: AblationBudget | None = None
    rubric: RubricDraft = Field(default_factory=RubricDraft)

    @field_validator("entrypoint")
    @classmethod
    def python_entrypoint(cls, value: str) -> str:
        path = relative_path(value)
        if not path.endswith(".py"):
            raise ValueError("research entrypoint must be a Python file")
        return path

    @field_validator("patch_paths", "test_paths")
    @classmethod
    def bounded_python_paths(cls, values: list[str]) -> list[str]:
        result = [cls.python_entrypoint(value) for value in values]
        if len(set(result)) != len(result):
            raise ValueError("duplicate patch paths")
        return result

    @model_validator(mode="after")
    def unique_variants(self) -> "ResearchSpec":
        if self.ablation_search and (self.ablations or self.test_paths):
            raise ValueError("choose automatic ablation search or fixed variants/pytest suites")
        if self.test_paths:
            if self.benchmark or self.arguments or self.ablations:
                raise ValueError("pytest suites cannot replace benchmarks or custom arguments")
            if set(self.test_paths) & {*self.patch_paths, self.entrypoint}:
                raise ValueError("selected pytest sources must remain unmodified")
        names = [variant.name for variant in self.ablations]
        if "baseline" in names or len(set(names)) != len(names):
            raise ValueError("ablation names must be unique and exclude baseline")
        if any(len(argument.encode()) > 4096 for argument in self.arguments):
            raise ValueError("experiment argument exceeds size limit")
        return self


class FilePatch(ContractModel):
    path: str
    base_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    content: str = Field(min_length=1, max_length=131072)


class ResearchPatch(ContractModel):
    version: Literal["research.patch/v1"] = "research.patch/v1"
    files: list[FilePatch] = Field(default_factory=list, max_length=3)
    rationale: str = Field(default="", max_length=4000)


class EnvironmentReport(ContractModel):
    python: str
    platform: str
    installed: dict[str, str]
    container_image: str
    container_image_id: str | None = None


class RunAttempt(ContractModel):
    number: int = Field(ge=1, le=3)
    patch_sha256: str
    patch: ResearchPatch
    exit_code: int | None
    stdout: str
    stderr: str
    error: str = ""
    phase: Literal["install", "execute", "resources"] = "execute"
    sandbox_id: str | None = None
    container_image_id: str | None = None
    environment_lock_sha256: str = ""
    runtime: RuntimeBinding | None = Field(default=None, exclude_if=lambda value: value is None)


class ResearchRun(ContractModel):
    variant: str
    arguments: list[str]
    attempts: list[RunAttempt]
    environment: EnvironmentReport | None = None
    predictions_sha256: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    execution_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    module_calls: dict[str, list[str]] = Field(default_factory=dict)
    reproduction: ReproductionDecision | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class BenchmarkPreflightReport(ContractModel):
    version: Literal["benchmark.preflight/v1"] = "benchmark.preflight/v1"
    status: Literal["passed", "failed"]
    dataset_sha256: str
    spec_sha256: str
    environment_lock_sha256: str
    patch_sha256: str
    sample_ids: list[str] = Field(min_length=1, max_length=8)
    total_rows: int = Field(ge=1)
    run: ResearchRun
    framework_source_sha256: str | None = None
    environment_lock: EnvironmentLock | None = None
    environment_changes: list[EnvironmentRepairDecision] = Field(default_factory=list, max_length=2)
    dependency_requests: list[str] = Field(default_factory=list, max_length=128)
    runtime: RuntimeBinding | None = Field(default=None, exclude_if=lambda value: value is None)


class SourceIntegrity(ContractModel):
    provided_code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prepared_entrypoint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ResearchReport(ContractModel):
    version: Literal["research.report/v1"] = "research.report/v1"
    repository_commit: str | None
    runs: list[ResearchRun]
    comparison: dict[str, dict[str, float]] = Field(default_factory=dict)
    success: bool
    dataset_upload_id: str | None = None
    dataset_sha256: str | None = None
    environment_lock_sha256: str = ""
    entrypoint: str = ""
    runtime: RuntimeBinding | None = Field(default=None, exclude_if=lambda value: value is None)
    reproduction: ReproductionDecision | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_integrity: SourceIntegrity | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    preflight: BenchmarkPreflightReport | None = None
    preflight_rechecks: list[BenchmarkPreflightReport] = Field(default_factory=list, max_length=2)
    environment_changes: list[EnvironmentRepairDecision] = Field(default_factory=list, max_length=2)
    final_environment_lock: EnvironmentLock | None = None
    environment_preparation: list[EnvironmentPreparationReport] = Field(
        default_factory=list, max_length=2
    )
    ablation_plan: AblationPlan | None = None
