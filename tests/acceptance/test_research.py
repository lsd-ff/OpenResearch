import hashlib
import io
import json
import os

import httpx
import pytest
from test_docker_smoke import pytestmark as pytestmark

from openresearch.llm.client import LLMClient
from openresearch.models.claims import ClaimDraft, CriterionDraft, RubricDraft
from openresearch.models.context import Identity
from openresearch.models.research import BenchmarkSpec, ExperimentVariant, ResearchSpec
from openresearch.models.runtime import Status
from openresearch.models.sources import EnvironmentSpec, RepositorySpec
from openresearch.models.submissions import PlanSubmission
from openresearch.sandbox.docker import DockerSandbox
from openresearch.scheduler.worker import Worker
from openresearch.services.plans import PlanService
from openresearch.services.uploads import UploadService
from openresearch.storage.upload_files import UploadSubmission

PREDICTOR = """import json, os, pathlib, sys
rows = json.loads(pathlib.Path(os.environ['OPENRESEARCH_INPUT']).read_text())
assert all('y' not in row['inputs'] for row in rows)
predictions = [{'id': row['id'], 'prediction': 'yes' if row['inputs']['x'] == '1' else 'no'}
               for row in rows]
if 'ablated' in sys.argv:
    predictions = [{'id': row['id'], 'prediction': 'no'} for row in rows]
pathlib.Path(os.environ['OPENRESEARCH_PREDICTIONS']).write_text(json.dumps(predictions))
print('real predictions written')
"""


async def finish(worker, repository, plan_id) -> None:
    import asyncio

    async with asyncio.timeout(120):
        while repository.get_plan(plan_id).status not in {Status.COMPLETED, Status.FAILED}:
            await worker.tick()
            await asyncio.sleep(0.02)
        await asyncio.gather(*(active.future for active in worker.active.values()))


def benchmark_plan(
    settings,
    repository,
    code=PREDICTOR,
    repairs=0,
    ablations=True,
    data=b"id,x,y\na,1,yes\nb,0,no\nc,1,yes\n",
    search=None,
    environment=None,
    intent="evaluate a real classifier",
    auto_design=False,
):
    identity = Identity("research-user", "research-session")
    upload = UploadService(settings, repository).create(
        UploadSubmission("dataset.csv", io.BytesIO(data), identity)
    )
    return PlanService(settings, repository).create(
        PlanSubmission(
            intent=intent,
            identity=identity,
            code=code,
            workflow="benchmark",
            auto_design=auto_design,
            research=ResearchSpec(
                environment=environment or EnvironmentSpec(),
                max_repairs=repairs,
                ablation_search=search,
                rubric=RubricDraft(
                    claims=[
                        ClaimDraft(
                            title="Classification accuracy",
                            statement="Baseline accuracy >= 0.9",
                            criteria=[
                                CriterionDraft(
                                    description="baseline accuracy",
                                    metric_name="accuracy",
                                    expected_value=0.9,
                                    operator="at_least",
                                ),
                                CriterionDraft(
                                    description="ablation accuracy",
                                    metric_name="accuracy",
                                    expected_value=0.9,
                                    operator="at_least",
                                    variant="without_feature",
                                ),
                            ]
                            if ablations
                            else [
                                CriterionDraft(
                                    description="baseline accuracy",
                                    metric_name="accuracy",
                                    expected_value=0.9,
                                    operator="at_least",
                                )
                            ],
                        )
                    ]
                ),
                benchmark=BenchmarkSpec(
                    upload_id=upload.id, input_columns=["x"], target_column="y", id_column="id"
                ),
                ablations=[ExperimentVariant(name="without_feature", arguments=["ablated"])]
                if ablations
                else [],
            ),
        )
    )


async def test_real_predictions_and_ablation_metrics(settings, repository, files) -> None:
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    plan = benchmark_plan(settings, repository)
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        report = json.loads(files.read(done.artifacts["research_report"]))
        assert report["preflight"]["status"] == "passed"
        assert report["preflight"]["sample_ids"] == ["a", "b", "c"]
        assert report["runs"][0]["metrics"]["accuracy"] == 1.0
        assert report["runs"][1]["metrics"]["accuracy"] == pytest.approx(1 / 3)
        assert report["comparison"]["without_feature"]["accuracy"] == pytest.approx(-2 / 3)
        assert report["runs"][0]["environment"]["python"].startswith("3.12.")
        assert len(json.loads(files.read(done.artifacts["benchmark_predictions"]))["baseline"]) == 3
        assert report["runs"][0]["predictions_sha256"]
        graph = json.loads(files.read(done.artifacts["claim_evidence_graph"]))
        assert graph["claims"][0]["status"] == "partially_reproduced"
        assert [item["status"] for item in graph["claims"][0]["criteria"]] == [
            "verified",
            "contradicted",
        ]
        rubric = json.loads(files.read(done.artifacts["claim_rubric"]))
        assert graph["rubric_sha256"] == rubric["sha256"]
        assert rubric["claims"][0]["criteria"][0]["expected_value"] == 0.9
    finally:
        await worker._shutdown()


async def test_failed_prediction_contract_preserves_evidence(settings, repository, files) -> None:
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    bad = (
        "import os, pathlib\n"
        "pathlib.Path(os.environ['OPENRESEARCH_PREDICTIONS']).write_text('[]')\n"
    )
    plan = benchmark_plan(settings, repository, code=bad, ablations=False)
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.FAILED
        report = json.loads(files.read(done.artifacts["benchmark_preflight_report"]))
        assert report["status"] == "failed" and report["run"]["metrics"] == {}
        assert "every dataset row" in report["run"]["attempts"][0]["error"]
        assert "research_report" not in done.artifacts
        assert next(node for node in done.nodes if node.type == "research_execute").run_count == 0
    finally:
        await worker._shutdown()


async def test_two_repairs_use_new_containers_and_replayable_final_patch(
    settings, repository, files
) -> None:
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    plan = benchmark_plan(
        settings, repository, code="raise RuntimeError('first failure')", repairs=2, ablations=False
    )
    calls = []

    def provider(request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content)["messages"]
        context = json.loads(
            next(item["content"] for item in reversed(messages) if item["role"] == "user")
        )
        current = context["allowed_paths"]["openresearch_run.py"]
        assert hashlib.sha256(current["content"].encode()).hexdigest() == current["base_sha256"]
        code = "raise RuntimeError('second failure')" if not calls else PREDICTOR
        calls.append(code)
        patch = {
            "files": [
                {
                    "path": "openresearch_run.py",
                    "base_sha256": current["base_sha256"],
                    "content": code,
                }
            ]
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(patch)}}]})

    settings.llm_model = "test-only"
    from pydantic import SecretStr

    settings.llm_api_key = SecretStr("test-only")
    worker.executor.research_executor.preparer.llm = LLMClient(
        settings, httpx.MockTransport(provider)
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        report = json.loads(files.read(done.artifacts["research_report"]))
        assert len(report["preflight"]["run"]["attempts"]) == 3 and len(calls) == 2
        assert len(report["runs"][0]["attempts"]) == 1
        final = json.loads(files.read(done.artifacts["final_patch"]))
        assert final["files"][0]["base_sha256"] is None
        assert final["files"][0]["content"] == PREDICTOR
    finally:
        await worker._shutdown()


@pytest.mark.external
@pytest.mark.skipif(
    os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1",
    reason="explicit external source opt-in required",
)
async def test_real_pinned_repository_and_offline_wheel_install(
    settings, repository, files
) -> None:
    worker = Worker(settings, repository, files, DockerSandbox(settings))
    code = """import sys
sys.path.insert(0, 'src')
from itsdangerous import URLSafeSerializer
import blinker
signer = URLSafeSerializer('local-test-secret')
assert signer.loads(signer.dumps({'value': 15})) == {'value': 15}
assert blinker.signal('real').name == 'real'
print('verified real repository and wheel')
"""
    plan = PlanService(settings, repository).create(
        PlanSubmission(
            intent="execute pinned repository source",
            identity=Identity("real", "real"),
            code=code,
            workflow="reproduce",
            research=ResearchSpec(
                repository=RepositorySpec(url="https://github.com/pallets/itsdangerous"),
                max_repairs=0,
                environment=EnvironmentSpec(requirements=["blinker==1.9.0"]),
            ),
        )
    )
    try:
        repository.queue_plan(plan.id, plan.owner_id)
        await finish(worker, repository, plan.id)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        snapshot = json.loads(files.read(done.artifacts["repository_snapshot"]))
        assert len(snapshot["commit"]) == 40 and any(
            item["path"].startswith("src/itsdangerous/") for item in snapshot["files"]
        )
        report = json.loads(files.read(done.artifacts["research_report"]))
        assert report["repository_commit"] == snapshot["commit"]
        assert report["runs"][0]["environment"]["installed"] == {"blinker": "1.9.0"}
        assert "verified real repository and wheel" in report["runs"][0]["attempts"][0]["stdout"]
    finally:
        await worker._shutdown()
