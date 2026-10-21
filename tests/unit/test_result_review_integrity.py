import copy
import io
import json

import pytest

from openresearch.models.claims import RubricDraft
from openresearch.models.context import Identity
from openresearch.models.execution import ExecutionLease
from openresearch.models.research import BenchmarkSpec, FilePatch, ResearchPatch, ResearchSpec
from openresearch.models.runtime import Status, new_id
from openresearch.models.sources import EnvironmentLock
from openresearch.models.submissions import PlanSubmission
from openresearch.research.review_research import digest
from openresearch.research.review_results import verify_results
from openresearch.research.review_sources import capture_review, load_review
from openresearch.research.rubrics import freeze_rubric
from openresearch.services.plans import PlanService
from openresearch.services.uploads import UploadService
from openresearch.storage.upload_files import UploadSubmission


def sealed_fixture(settings, repository, files, mutation=None):
    """合成不可变记录夹具，仅验证审核契约；不宣称这里执行过实验。"""
    upload = UploadService(settings, repository).create(
        UploadSubmission(
            "fixture.csv",
            io.BytesIO(b"id,x,y\na,1,yes\nb,0,no\nc,1,yes\n"),
            Identity("alice", "fixture"),
        )
    )
    code = "print('synthetic receipt only')\n"
    spec = ResearchSpec(
        benchmark=BenchmarkSpec(
            upload_id=upload.id, input_columns=["x"], target_column="y", id_column="id"
        )
    )
    plan = PlanService(settings, repository).build(
        PlanSubmission(
            "synthetic source fixture",
            Identity("alice", "fixture"),
            code,
            workflow="benchmark",
            research=spec,
        )
    )
    patch = ResearchPatch(files=[FilePatch(path=spec.entrypoint, content=code)])
    lock = EnvironmentLock()
    attempt = {
        "number": 1,
        "patch_sha256": digest(patch.model_dump_json()),
        "patch": patch.model_dump(),
        "exit_code": 0,
        "stdout": "fixture",
        "stderr": "",
        "sandbox_id": "formal-fixture",
        "environment_lock_sha256": digest(lock.model_dump_json()),
    }
    run = {
        "variant": "baseline",
        "arguments": [],
        "attempts": [attempt],
        "environment": {
            "python": "3.12.0",
            "platform": "Linux",
            "installed": {},
            "container_image": "fixture",
        },
        "metrics": {"accuracy": 1.0},
        "predictions_sha256": "1" * 64,
    }
    sample_run = copy.deepcopy(run)
    sample_run["attempts"][0]["sandbox_id"] = "preflight-fixture"
    report = {
        "repository_commit": None,
        "runs": [run],
        "success": True,
        "dataset_upload_id": upload.id,
        "dataset_sha256": upload.sha256,
        "entrypoint": spec.entrypoint,
        "environment_lock_sha256": digest(lock.model_dump_json()),
        "final_environment_lock": lock.model_dump(),
        "source_integrity": {
            "provided_code_sha256": digest(code),
            "prepared_entrypoint_sha256": digest(code),
        },
        "preflight": {
            "status": "passed",
            "dataset_sha256": upload.sha256,
            "spec_sha256": digest(spec.model_dump_json()),
            "environment_lock_sha256": digest(lock.model_dump_json()),
            "patch_sha256": digest(patch.model_dump_json()),
            "sample_ids": ["a", "b", "c"],
            "total_rows": 3,
            "run": sample_run,
        },
    }
    predictions = {
        "baseline": [
            {"id": "a", "prediction": "yes"},
            {"id": "b", "prediction": "no"},
            {"id": "c", "prediction": "yes"},
        ]
    }
    if mutation:
        mutation(report, predictions)
    values = {
        "environment_lock": lock.model_dump_json(),
        "claim_rubric": freeze_rubric(RubricDraft(), None).model_dump_json(),
        "research_patch": patch.model_dump_json(),
        "benchmark_preflight_patch": patch.model_dump_json(),
        "benchmark_preflight_report": json.dumps(report["preflight"]),
        "research_report": json.dumps(report),
        "final_patch": patch.model_dump_json(),
        "benchmark_predictions": json.dumps(predictions),
    }
    values.update(
        claim_evidence_graph='{"test_fixture_only":true}',
        claim_verification_report="synthetic fixture only",
    )
    for task in plan.nodes:
        task.status, task.run_count = Status.COMPLETED, 1
        lease = ExecutionLease(plan.id, task.id, new_id(), "test-fixture")
        for key in task.output_artifacts:
            if key in values:
                plan.artifacts[key] = files.write(lease, key, values[key])
    plan.status = Status.COMPLETED
    repository.save_plan(plan)
    return load_review(capture_review(plan), "alice", repository, files)


def test_review_recomputes_metrics_from_frozen_predictions_and_declares_raw_byte_limit(
    settings, repository, files
):
    material = sealed_fixture(settings, repository, files)
    verified = verify_results(material, settings, repository, files)
    assert verified.audit.integrity_valid and verified.audit.status == "verified"
    assert (
        verified.audit.metrics == {"baseline": {"accuracy": 1.0}}
        and verified.audit.sample_count == 3
    )
    assert any("原始预测字节" in item for item in verified.audit.limitations)


@pytest.mark.parametrize(
    "case",
    [
        "metric",
        "duplicate_id",
        "dataset",
        "patch",
        "lock",
        "preflight",
        "shared_sandbox",
        "false_success",
        "nonfinite",
    ],
)
def test_review_rejects_forged_but_hash_sealed_execution_records(settings, repository, files, case):
    def change(report, predictions):
        if case == "metric":
            report["runs"][0]["metrics"]["accuracy"] = 0.0
        elif case == "duplicate_id":
            predictions["baseline"][1]["id"] = "a"
        elif case == "dataset":
            report["dataset_sha256"] = "0" * 64
        elif case == "patch":
            report["runs"][0]["attempts"][0]["patch_sha256"] = "0" * 64
        elif case == "lock":
            report["environment_lock_sha256"] = "0" * 64
        elif case == "preflight":
            report["preflight"]["patch_sha256"] = "0" * 64
        elif case == "shared_sandbox":
            report["preflight"]["run"]["attempts"][0]["sandbox_id"] = "formal-fixture"
        elif case == "false_success":
            report["runs"][0]["attempts"][0]["exit_code"] = 1
        else:
            report["runs"][0]["metrics"]["accuracy"] = float("nan")

    material = sealed_fixture(settings, repository, files, change)
    audit = verify_results(material, settings, repository, files).audit
    assert not audit.integrity_valid and audit.status == "failed" and audit.errors
