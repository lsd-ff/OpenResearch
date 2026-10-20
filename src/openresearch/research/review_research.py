import hashlib
import json
import math

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.audit import ResultAudit
from openresearch.models.claims import FrozenRubric
from openresearch.models.research import ResearchPatch, ResearchReport, ResearchSpec
from openresearch.models.runtime import Status
from openresearch.models.sources import EnvironmentLock, RepositorySnapshot
from openresearch.research.benchmark import Dataset, evaluate
from openresearch.research.outcomes import comparison
from openresearch.research.patches import apply_patch, load_sources
from openresearch.research.review_sources import ReviewMaterial
from openresearch.research.rubrics import verify_rubric
from openresearch.research.specifications import resolved_research
from openresearch.services.uploads import UploadService
from openresearch.sources.blobs import SourceBlobs
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def verify_receipts(report: ResearchReport, material: ReviewMaterial, audit: ResultAudit) -> None:
    require(0 < len(report.runs) <= 7, "research receipt has no bounded actual runs")
    require(report.runs[0].variant == "baseline", "research receipt has no leading baseline")
    require(len({run.variant for run in report.runs}) == len(report.runs), "duplicate run variants")
    completed = []
    for run in report.runs:
        require(0 < len(run.attempts) <= 3, "run has no bounded execution history")
        for attempt in run.attempts:
            require(
                attempt.patch_sha256 == digest(attempt.patch.model_dump_json()),
                "run patch hash mismatch",
            )
        require(
            all(math.isfinite(value) for value in run.metrics.values()),
            "research metric is not finite",
        )
        final = run.attempts[-1]
        passed = not final.error and final.exit_code == 0 and final.phase == "execute"
        if passed:
            require(
                bool(final.sandbox_id) and run.environment is not None,
                "successful run lacks sandbox/environment receipt",
            )
        completed.append(passed)
    require(report.success == all(completed), "reported success differs from actual final receipts")
    require(
        (material.task.status == Status.COMPLETED) == report.success,
        "source task status differs from its research receipt",
    )
    audit.execution_success = report.success
    audit.status = "verified" if report.success else "failed"
    audit.checks.append("最终退出码、执行阶段、沙箱/环境回执及逐次补丁摘要一致")


def verify_workspace(
    report: ResearchReport,
    spec: ResearchSpec,
    material: ReviewMaterial,
    settings: Settings,
    files: ArtifactStore,
    audit: ResultAudit,
) -> None:
    artifacts = material.plan.artifacts
    snapshot = (
        RepositorySnapshot.model_validate_json(files.read(artifacts["repository_snapshot"]))
        if "repository_snapshot" in artifacts
        else None
    )
    require(
        report.repository_commit == (snapshot.commit if snapshot else None),
        "research repository commit mismatch",
    )
    original = load_sources(
        snapshot, SourceBlobs(settings.data_dir / "sources", settings.source_max_file_bytes)
    )
    patch = ResearchPatch.model_validate_json(files.read(artifacts["final_patch"]))
    if report.success:
        require(
            all(
                run.attempts[-1].patch_sha256 == digest(patch.model_dump_json())
                for run in report.runs
            ),
            "final patch was not the patch used by each successful run",
        )
    else:
        audit.limitations.append(
            "失败历史中的准备补丁不必都已进入正式执行；实际测试代码以逐次回执为准。"
        )
    prepared = apply_patch(original, patch, spec)
    require(
        report.entrypoint == spec.entrypoint,
        "research entrypoint differs from frozen specification",
    )
    if report.source_integrity:
        identity = report.source_integrity
        require(
            identity.prepared_entrypoint_sha256 == digest(prepared[spec.entrypoint]),
            "prepared entrypoint hash mismatch",
        )
        source_task = next(node for node in material.plan.nodes if node.id == material.task.id)
        supplied = next(
            (
                node.inputs.get("code")
                for node in material.plan.nodes
                if node.type == "research_prepare"
                and node.artifact_scope == source_task.artifact_scope
            ),
            None,
        )
        require(
            isinstance(supplied, str) and digest(supplied) == identity.provided_code_sha256,
            "provided source hash mismatch",
        )
    initial = EnvironmentLock.model_validate_json(files.read(artifacts["environment_lock"]))
    verify_environments(report, initial, audit)
    rubric = FrozenRubric.model_validate_json(files.read(artifacts["claim_rubric"]))
    verify_rubric(rubric)
    audit.checks.append("固定仓库、准备入口/补丁和冻结验收摘要一致")


def verify_environments(
    report: ResearchReport, initial: EnvironmentLock, audit: ResultAudit
) -> None:
    lock = report.final_environment_lock or initial
    require(
        digest(lock.model_dump_json()) == report.environment_lock_sha256,
        "final environment lock hash mismatch",
    )
    checks = ([report.preflight] if report.preflight else []) + report.preflight_rechecks
    changes = report.environment_changes + [
        change for check in checks for change in check.environment_changes
    ]
    candidates = [
        initial,
        lock,
        *[check.environment_lock for check in checks],
        *[change.environment_lock for change in changes],
    ]
    known = {digest(item.model_dump_json()): item for item in candidates if item is not None}
    checked_identity = False
    for run in report.runs:
        final = run.attempts[-1]
        if report.success:
            require(
                final.environment_lock_sha256 == report.environment_lock_sha256,
                "successful run environment lock differs from final lock",
            )
        actual = known.get(final.environment_lock_sha256)
        if actual is None:
            require(not report.success, "successful run lock body is unavailable")
            audit.limitations.append("失败历史中有运行锁仅保留摘要，未宣称已复核该锁正文或安装包。")
            continue
        if run.environment:
            expected = dict(wheel.requirement.split("==", 1) for wheel in actual.wheels)
            require(
                run.environment.installed == expected, "installed package receipt differs from lock"
            )
            if actual.interpreter:
                require(
                    run.environment.python == actual.interpreter.python_full_version,
                    "observed interpreter differs from lock",
                )
                checked_identity = True
                if actual.interpreter.container_image_id:
                    require(
                        run.environment.container_image_id == actual.interpreter.container_image_id,
                        "observed image differs from lock",
                    )
    audit.checks.append("对可取得的实际运行锁核对了安装包回执；成功运行使用最终锁")
    if checked_identity:
        audit.checks.append("已核对锁中固定的实际解释器完整版本")
    else:
        audit.limitations.append(
            "锁或安装回执不足以独立核验完整解释器身份，未宣称已核验完整版本/镜像身份。"
        )
    if lock.interpreter and not lock.interpreter.container_image_id:
        audit.limitations.append("该运行时未固定可核验的镜像摘要。")


def verify_variants(report: ResearchReport, spec: ResearchSpec) -> None:
    expected = {
        "baseline": spec.arguments,
        **{variant.name: variant.arguments for variant in spec.ablations},
    }
    actual = [run.variant for run in report.runs]
    require(actual == list(expected)[: len(actual)], "run variants differ from frozen order")
    if report.success:
        require(
            len(actual) == len(expected), "successful report omitted a declared experiment variant"
        )
    require(
        all(run.arguments == expected[run.variant] for run in report.runs),
        "actual run arguments differ from frozen variant",
    )


def verify_benchmark(
    report: ResearchReport,
    spec: ResearchSpec,
    material: ReviewMaterial,
    settings: Settings,
    repository: Repository,
    files: ArtifactStore,
    audit: ResultAudit,
) -> None:
    if spec.benchmark is None:
        require(
            report.dataset_upload_id is None
            and report.dataset_sha256 is None
            and not any(run.metrics for run in report.runs),
            "numeric results have no frozen independently verifiable benchmark protocol",
        )
        audit.limitations.append("没有声明独立 Benchmark 数据；零退出码不证明论文数值效果。")
        return
    from openresearch.research.benchmark import parse_dataset

    _, raw = UploadService(settings, repository).content(
        spec.benchmark.upload_id, material.plan.owner_id
    )
    dataset = parse_dataset(raw, spec.benchmark, settings.benchmark_max_rows)
    require(
        report.dataset_upload_id == spec.benchmark.upload_id
        and report.dataset_sha256 == dataset.sha256,
        "research dataset differs from frozen uploaded data",
    )
    predictions = json.loads(files.read(material.plan.artifacts["benchmark_predictions"]))
    require(
        isinstance(predictions, dict) and set(predictions) == {run.variant for run in report.runs},
        "prediction variants do not match actual runs",
    )
    allowed = {"baseline", *(variant.name for variant in spec.ablations)}
    require(not set(predictions) - allowed, "run variant is absent from frozen specification")
    for run in report.runs:
        if run.attempts[-1].error:
            require(
                not run.metrics and not predictions[run.variant],
                "failed run cannot publish successful metrics or predictions",
            )
            continue
        metrics = evaluate(dataset, predictions[run.variant], spec.benchmark)
        for key, value in metrics.items():
            require(
                key in run.metrics and value == run.metrics[key],
                "metric differs from recomputed predictions: " + key,
            )
        extras = set(run.metrics) - set(metrics)
        require(
            not extras - {"framework_module_usage", "prediction_coverage"},
            "unverified additional benchmark metric",
        )
        if extras:
            require(
                bool(run.module_calls)
                and all(run.module_calls.values())
                and all(run.metrics[key] == 1.0 for key in extras),
                "framework trace/coverage metric is inconsistent",
            )
        audit.metrics[run.variant] = dict(run.metrics)
    require(
        report.comparison == comparison(report.runs),
        "ablation deltas differ from actual run metrics",
    )
    verify_sample_runs(report, dataset)
    audit.sample_count, audit.dataset_sha256 = len(dataset.targets), dataset.sha256
    audit.checks.append("所属数据 SHA、预测 ID/覆盖、每个实际运行指标和消融差值已独立重算")
    audit.limitations.append(
        "原始预测字节未单独留存；复核使用冻结的结构化预测，不声称逐字节重现原文件。"
    )
    audit.limitations.append(
        "预检预测未单独留存，已复核其真实回执及样本声明，未用正式预测冒充预检重算。"
    )


def verify_sample_runs(report: ResearchReport, dataset: Dataset) -> None:
    checks = ([report.preflight] if report.preflight else []) + report.preflight_rechecks
    require(bool(checks), "benchmark receipt lacks independent preflight")
    for check in checks:
        require(
            check.dataset_sha256 == dataset.sha256 and check.total_rows == len(dataset.targets),
            "preflight dataset identity mismatch",
        )
        require(
            check.sample_ids == list(dataset.targets)[:8],
            "preflight sample IDs differ from actual bounded prefix",
        )
        if check.status == "passed":
            # 预检预测未单独留存，不能拿正式预测代替；只核对其实际回执和覆盖声明。
            require(
                bool(check.run.attempts)
                and not check.run.attempts[-1].error
                and check.run.attempts[-1].exit_code == 0,
                "passed preflight has a failed execution receipt",
            )
    if report.success:
        last = checks[-1]
        require(
            last.status == "passed"
            and last.patch_sha256 == report.runs[0].attempts[-1].patch_sha256
            and last.environment_lock_sha256 == report.environment_lock_sha256,
            "final run used stale or failed preflight",
        )
        require(
            bool(last.run.attempts[-1].sandbox_id)
            and last.run.attempts[-1].sandbox_id != report.runs[0].attempts[-1].sandbox_id,
            "preflight and full run did not use independent sandboxes",
        )


def review_research(
    material: ReviewMaterial,
    settings: Settings,
    repository: Repository,
    files: ArtifactStore,
    audit: ResultAudit,
) -> ResearchReport:
    report = ResearchReport.model_validate_json(
        files.read(material.plan.artifacts["research_report"])
    )
    spec = resolved_research(material.plan, material.task, files)
    audit.kind = "research"
    verify_receipts(report, material, audit)
    verify_variants(report, spec)
    verify_workspace(report, spec, material, settings, files, audit)
    verify_benchmark(report, spec, material, settings, repository, files, audit)
    return report
