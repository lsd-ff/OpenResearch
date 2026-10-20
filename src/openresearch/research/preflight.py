import hashlib
from dataclasses import replace

from openresearch.errors import ContractError
from openresearch.models.research import BenchmarkPreflightReport, ExperimentVariant
from openresearch.research.benchmark import Dataset
from openresearch.research.repairs import RepairPipeline
from openresearch.research.runner import ExperimentInput


def preflight_sample(dataset: Dataset) -> Dataset:
    inputs = dataset.inputs[:8]
    identifiers = {str(row["id"]) for row in inputs}
    return Dataset(
        inputs=inputs,
        targets={key: value for key, value in dataset.targets.items() if key in identifiers},
        sha256=dataset.sha256,
    )


def checksum(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class BenchmarkPreflight:
    def __init__(self, repairs: RepairPipeline) -> None:
        self.repairs = repairs

    async def run(
        self, intent: str, original: dict[str, bytes], request: ExperimentInput
    ) -> BenchmarkPreflightReport:
        if request.dataset is None or request.spec.benchmark is None:
            raise ContractError("benchmark preflight requires actual uploaded data")
        sample = preflight_sample(request.dataset)
        # 预检和全量运行使用不同容器；目标值仍只留在服务端指标校验器。
        probe = replace(
            request,
            dataset=sample,
            variant=ExperimentVariant(name="preflight", arguments=request.spec.arguments),
        )
        outcome = await self.repairs.run(intent, original, probe)
        run = outcome.output.run
        return BenchmarkPreflightReport(
            status="failed" if run.attempts[-1].error else "passed",
            dataset_sha256=request.dataset.sha256,
            spec_sha256=checksum(request.spec.model_dump_json()),
            environment_lock_sha256=checksum(request.lock.model_dump_json()),
            patch_sha256=checksum(outcome.request.patch.model_dump_json()),
            sample_ids=[str(row["id"]) for row in sample.inputs],
            total_rows=len(request.dataset.inputs),
            run=run,
        )


def verify_preflight(report: BenchmarkPreflightReport, request: ExperimentInput) -> None:
    dataset = request.dataset
    if (
        dataset is None
        or report.status != "passed"
        or report.dataset_sha256 != dataset.sha256
        or report.total_rows != len(dataset.inputs)
        or report.sample_ids != [str(row["id"]) for row in dataset.inputs[:8]]
        or report.patch_sha256 != checksum(request.patch.model_dump_json())
        or report.spec_sha256 != checksum(request.spec.model_dump_json())
        or report.environment_lock_sha256 != checksum(request.lock.model_dump_json())
    ):
        raise ContractError(
            "preflight does not match the full experiment's code, data or environment"
        )
