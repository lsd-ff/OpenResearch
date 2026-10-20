import json
from dataclasses import dataclass

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.audit import ResultAudit, ReviewSnapshot
from openresearch.models.recovery import CodeRecoveryReport
from openresearch.models.research import ResearchReport
from openresearch.models.runtime import Status
from openresearch.research.data_profile import profile_records, read_records
from openresearch.research.review_research import digest, require, review_research
from openresearch.research.review_sources import ReviewMaterial
from openresearch.services.uploads import UploadService
from openresearch.storage.files import ArtifactStore
from openresearch.storage.repository import Repository


@dataclass(frozen=True)
class VerifiedResults:
    audit: ResultAudit
    research: ResearchReport | None = None


def empty_audit(snapshot: ReviewSnapshot) -> ResultAudit:
    return ResultAudit(
        source_plan_id=snapshot.source_plan_id,
        source_task_id=snapshot.source_task_id,
        source_status=snapshot.source_status,
        input_sha256={
            **{"source:" + key: value.sha256 for key, value in snapshot.artifacts.items()},
            **{"supplement:" + key: value.sha256 for key, value in snapshot.supplements.items()},
        },
    )


def review_code(material: ReviewMaterial, files: ArtifactStore, audit: ResultAudit) -> None:
    artifacts = material.plan.artifacts
    report = CodeRecoveryReport.model_validate_json(files.read(artifacts["code_recovery_report"]))
    require(bool(report.attempts), "code receipt has no actual attempts")
    for attempt in report.attempts:
        require(digest(attempt.code) == attempt.code_sha256, "code attempt source hash mismatch")
    code = files.read(artifacts["executed_code"])
    require(
        digest(code) == report.final_code_sha256 == report.attempts[-1].code_sha256,
        "final executed source hash mismatch",
    )
    generated = files.read(artifacts["generated_code"])
    require(
        digest(generated) == report.original_code_sha256, "original generated source hash mismatch"
    )
    final = report.attempts[-1]
    passed = bool(
        final.execution_requested
        and final.output
        and final.output.exit_code == 0
        and not final.error
        and final.phase in {"execute", "outputs"}
    )
    require(
        report.success == passed and (material.task.status == Status.COMPLETED) == passed,
        "code success differs from actual execution receipt",
    )
    metrics = json.loads(files.read(artifacts["run_metrics"]))
    if final.output:
        require(
            all(metrics.get(key) == value for key, value in final.output.model_dump().items()),
            "code output differs from saved run metrics",
        )
    if final.environment:
        expected = dict(wheel.requirement.split("==", 1) for wheel in final.environment_lock.wheels)
        require(
            final.environment.installed == expected,
            "code installed packages differ from actual lock",
        )
        if final.environment_lock.interpreter:
            require(
                final.environment.python == final.environment_lock.interpreter.python_full_version,
                "code interpreter differs from frozen lock",
            )
    audit.kind = "code"
    audit.execution_success = passed if final.execution_requested else None
    audit.status = (
        "verified" if passed else "failed" if final.execution_requested else "unverifiable"
    )
    audit.metrics = {
        "execution": {
            "program_attempts": float(sum(item.execution_requested for item in report.attempts)),
            "accepted_proposals": float(sum(item.accepted for item in report.decisions)),
        }
    }
    if final.output:
        audit.metrics["execution"]["final_exit_code"] = float(final.output.exit_code)
    audit.checks.append("原始/最终源码与逐次回执摘要一致")
    if final.execution_requested and final.output:
        audit.checks.append("实际程序退出码与保存输出一致")
    else:
        audit.limitations.append(
            "回执未证明实际程序已经执行，不能把安装/资源阶段输出作为实验结果。"
        )
    audit.limitations.append("程序断言与零退出码仅覆盖该程序实际执行范围，不证明论文完整效果。")


def review_data(
    material: ReviewMaterial,
    settings: Settings,
    repository: Repository,
    files: ArtifactStore,
    audit: ResultAudit,
) -> None:
    profile = json.loads(files.read(material.plan.artifacts["data_profile"]))
    record, raw = UploadService(settings, repository).content(
        material.task.inputs["upload_id"], material.plan.owner_id
    )
    expected = profile_records(read_records(raw, record.name, settings.benchmark_max_rows))
    require(
        profile.get("source")
        == {"upload_id": record.id, "name": record.name, "sha256": record.sha256},
        "data profile source identity mismatch",
    )
    require(
        {key: value for key, value in profile.items() if key != "source"} == expected,
        "saved data statistics differ from actual uploaded data",
    )
    audit.kind = "data"
    audit.status = "verified" if material.task.status == Status.COMPLETED else "failed"
    audit.sample_count, audit.dataset_sha256 = profile["row_count"], record.sha256
    audit.metrics = {
        key: {"missing": float(value["missing"]), "count": float(value["count"])}
        for key, value in profile["columns"].items()
    }
    audit.checks.append("所属上传 SHA、行数和逐列统计已从原文件重新计算")
    audit.limitations.append("数据剖析只验证所上传数据的统计，不属于模型性能或论文效果。")


def verify_results(
    material: ReviewMaterial, settings: Settings, repository: Repository, files: ArtifactStore
) -> VerifiedResults:
    audit = empty_audit(material.snapshot)
    report = None
    try:
        artifacts = material.plan.artifacts
        produced = [artifacts[key] for key in material.task.output_artifacts if key in artifacts]
        require(
            all(item.producer_task_id == material.task.id for item in produced),
            "result output belongs to another producer",
        )
        require(
            len({item.execution_id for item in produced}) <= 1,
            "result outputs mix different execution attempts",
        )
        if material.task.status == Status.COMPLETED:
            require(
                len(produced) == len(material.task.output_artifacts),
                "completed source is missing declared outputs",
            )
        if "research_report" in artifacts:
            report = review_research(material, settings, repository, files, audit)
        elif "code_recovery_report" in artifacts:
            review_code(material, files, audit)
        elif "data_profile" in artifacts:
            review_data(material, settings, repository, files, audit)
        else:
            audit.limitations.append(
                "源阶段没有留下可核验的实际执行/数据回执，不能从缺失产物推断成功。"
            )
    except (ContractError, ValueError, KeyError, OSError) as exc:
        audit.integrity_valid, audit.status = False, "failed"
        audit.errors.append(
            str(exc)[:2000] if not isinstance(exc, OSError) else "source artifact unavailable"
        )
    return VerifiedResults(audit, report)
