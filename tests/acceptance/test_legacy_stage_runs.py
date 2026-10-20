import asyncio
import hashlib
import json

import httpx
from conftest import make_repository_archive
from test_docker_smoke import pytestmark as pytestmark
from test_documents import real_pdf
from test_legacy_api import completed_result
from test_legacy_api import legacy_docker as legacy_docker
from test_research import PREDICTOR

from openresearch.agents.claim_freeze import ClaimFreezer
from openresearch.models.claims import RubricDraft
from openresearch.models.runtime import Status
from openresearch.sources.github import GitHubSource


def request(kind, inputs):
    return {
        "task_id": "go-stage-client-id",
        "task_type": kind,
        "task_description": "按原代码逐阶段运行，不要自动修复，不替换断言或数据",
        "assigned_to": "research_coding_agent",
        "inputs": inputs,
    }


async def fresh_benchmark(client, code):
    data = ("id,x,y\n" + "".join(f"{index},1,yes\n" for index in range(12))).encode()
    uploaded = await client.post("/api/uploads", files={"file": ("actual.csv", data, "text/csv")})
    assert uploaded.status_code == 201
    return {
        "code": code,
        "research": {
            "max_repairs": 0,
            "benchmark": {
                "upload_id": uploaded.json()["id"],
                "input_columns": ["x"],
                "target_column": "y",
                "id_column": "id",
            },
            "rubric": {
                "claims": [
                    {
                        "title": "fixed functional criterion",
                        "statement": "accuracy >= 0.9",
                        "criteria": [
                            {
                                "description": "baseline accuracy",
                                "metric_name": "accuracy",
                                "expected_value": 0.9,
                                "operator": "at_least",
                            }
                        ],
                    }
                ]
            },
        },
    }


async def test_old_stage_chain_runs_eight_then_twelve_actual_samples_without_repeating_work(
    legacy_docker, repository, files
):
    client, sandbox = legacy_docker
    code = PREDICTOR.replace(
        "print('real predictions written')", "print('ACTUAL_STAGE_ROWS', len(rows))"
    )
    async with asyncio.timeout(240):
        prepared = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "benchmark_adapter_generate",
                    await fresh_benchmark(client, code),
                ),
            )
        )
        partial = repository.get_plan(prepared["plan_id"], "alice")
        assert partial.status == Status.PENDING and len(partial.nodes) == 5
        assert (
            "benchmark_preflight_report" not in partial.artifacts
            and "research_report" not in partial.artifacts
        )
        frozen = {key: artifact.sha256 for key, artifact in partial.artifacts.items()}
        assert not await sandbox.inventory()
        preflight = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "benchmark_adapter_preflight",
                    {"workflow_ref": prepared["workflow_ref"]},
                ),
            )
        )
        sample = json.loads(preflight["outputs"]["benchmark_preflight_report"])
        assert sample["status"] == "passed" and len(sample["sample_ids"]) == 8
        assert "ACTUAL_STAGE_ROWS 8" in sample["run"]["attempts"][-1]["stdout"]
        before = repository.get_plan(prepared["plan_id"])
        assert before.status == Status.PENDING and "research_report" not in before.artifacts
        started = before.usage.started_at
        final = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "benchmark_execute",
                    {"workflow_ref": prepared["workflow_ref"]},
                ),
            )
        )
        done = repository.get_plan(prepared["plan_id"])
        assert final["plan_id"] == preflight["plan_id"] == prepared["plan_id"]
        assert done.status == Status.COMPLETED and done.usage.started_at == started
        assert all(node.run_count == 1 for node in done.nodes) and done.usage.task_attempts == 5
        assert all(done.artifacts[key].sha256 == digest for key, digest in frozen.items())
        report = json.loads(final["outputs"]["research_report"])
        baseline = report["runs"][0]
        assert report["success"] and baseline["metrics"]["accuracy"] == 1.0
        assert "ACTUAL_STAGE_ROWS 12" in baseline["attempts"][-1]["stdout"]
        assert baseline["attempts"][-1]["sandbox_id"] != sample["run"]["attempts"][-1]["sandbox_id"]
        assert (
            report["source_integrity"]["provided_code_sha256"]
            == hashlib.sha256(code.encode()).hexdigest()
        )
        assert (
            report["source_integrity"]["prepared_entrypoint_sha256"]
            == report["source_integrity"]["provided_code_sha256"]
        )
        assert len(json.loads(final["outputs"]["benchmark_predictions"])["baseline"]) == 12
        replay = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "benchmark_execute",
                    {"workflow_ref": prepared["workflow_ref"]},
                ),
            )
        )
        assert replay["outputs"] == final["outputs"]
        assert repository.get_plan(done.id).usage.task_attempts == 5
    assert not await sandbox.inventory() and not repository.cleanup_candidates(sandbox.backend)


async def test_failed_old_preflight_blocks_full_stage_and_replay_does_not_execute(
    legacy_docker, repository, files
):
    client, sandbox = legacy_docker
    code = PREDICTOR + "\nraise RuntimeError('ACTUAL_LEGACY_PREFLIGHT_REFUSED')\n"
    async with asyncio.timeout(180):
        failed = await client.post(
            "/api/execute",
            json=request(
                "benchmark_adapter_preflight",
                await fresh_benchmark(client, code),
            ),
        )
        assert failed.status_code == 200 and "event: error" in failed.text
        identifier = failed.headers["x-openresearch-plan-id"]
        plan = repository.get_plan(identifier)
        assert plan.status == Status.FAILED
        preflight = json.loads(files.read(plan.artifacts["benchmark_preflight_report"]))
        assert preflight["status"] == "failed" and len(preflight["sample_ids"]) == 8
        assert "ACTUAL_LEGACY_PREFLIGHT_REFUSED" in preflight["run"]["attempts"][-1]["stderr"]
        final = next(task for task in plan.nodes if task.type == "research_execute")
        assert final.run_count == 0 and final.status == Status.BLOCKED
        blocked = await client.post(
            "/api/execute",
            json=request(
                "benchmark_execute",
                {"workflow_ref": "openresearch:" + identifier},
            ),
        )
        assert "event: error" in blocked.text and "event: result" not in blocked.text
        assert repository.get_plan(identifier).usage.task_attempts == plan.usage.task_attempts
        assert "research_report" not in repository.get_plan(identifier).artifacts
    assert not await sandbox.inventory()


async def test_paper_code_execute_runs_actual_original_assertions_through_scientific_actor(
    legacy_docker, repository
):
    client, sandbox = legacy_docker
    code = (
        "values = [2, 3, 4]\nassert sum(x*x for x in values) == 29\n"
        "print('ACTUAL_SCIENTIFIC_STAGE', 29)\n"
    )
    async with asyncio.timeout(120):
        actual = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "paper_code_execute",
                    {"code": code, "research": {"max_repairs": 0}},
                ),
            )
        )
    report = json.loads(actual["outputs"]["research_report"])
    assert (
        report["success"]
        and "ACTUAL_SCIENTIFIC_STAGE 29" in report["runs"][0]["attempts"][-1]["stdout"]
    )
    assert (
        report["source_integrity"]["prepared_entrypoint_sha256"]
        == report["source_integrity"]["provided_code_sha256"]
        == hashlib.sha256(code.encode()).hexdigest()
    )
    assert repository.get_plan(actual["plan_id"]).status == Status.COMPLETED
    assert not await sandbox.inventory()


async def test_claim_stage_extracts_actual_pdf_pages_and_does_not_prepare_or_run_repository(
    legacy_docker, repository, monkeypatch
):
    client, sandbox = legacy_docker
    upload = await client.post(
        "/api/uploads", files={"file": ("paper.pdf", real_pdf(), "application/pdf")}
    )

    async def controlled_extract(self, corpus):
        assert "Research result is 0.75." in corpus.documents[0].pages[0].text
        return RubricDraft.model_validate(
            {
                "paper_title": "Controlled extraction from actual PDF",
                "claims": [
                    {
                        "title": "Published test figure",
                        "statement": "The result is 0.75.",
                        "source": {
                            "upload_id": upload.json()["id"],
                            "page": 1,
                            "quote": "Research result is 0.75.",
                        },
                        "criteria": [
                            {
                                "description": "stated accuracy",
                                "metric_name": "accuracy",
                                "expected_value": 0.75,
                            }
                        ],
                    }
                ],
            }
        )

    monkeypatch.setattr(ClaimFreezer, "_extract", controlled_extract)
    payload = request("claim_rubric_extract", {"attachments": [upload.json()["id"]]})
    payload["assigned_to"] = "librarian_agent"
    async with asyncio.timeout(90):
        actual = completed_result(await client.post("/api/execute", json=payload))
    rubric = json.loads(actual["outputs"]["claim_rubric"])
    assert rubric["claims"][0]["source_sha256"] == upload.json()["sha256"]
    assert rubric["claims"][0]["source_locator"].endswith(":page:1")
    assert json.loads(actual["outputs"]["claim_rubric_report"])["claim_count"] == 1
    partial = repository.get_plan(actual["plan_id"])
    assert partial.status == Status.PENDING
    assert all(
        task.run_count == 0
        for task in partial.nodes
        if task.type not in {"document_extract", "claim_freeze"}
    )
    assert not await sandbox.inventory()


async def test_ablation_design_stops_before_running_then_continues_same_selected_candidate(
    legacy_docker, repository
):
    client, sandbox = legacy_docker
    inputs = await fresh_benchmark(client, PREDICTOR)
    inputs["research"]["ablation_search"] = {"max_experiments": 1, "max_wall_minutes": 1}
    payload = request("ablation_design", inputs)
    payload["assigned_to"] = "data_agent"
    async with asyncio.timeout(180):
        design = completed_result(await client.post("/api/execute", json=payload))
        selection = json.loads(design["outputs"]["ablation_plan"])
        assert selection["generation_source"] == "source_fallback"
        assert selection["scoring_source"] == "deterministic_prior"
        assert selection["selected"][0]["arguments"] == ["ablated"]
        partial = repository.get_plan(design["plan_id"])
        assert partial.usage.task_attempts == 1 and "environment_lock" not in partial.artifacts
        actual = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "benchmark_execute",
                    {"workflow_ref": design["workflow_ref"]},
                ),
            )
        )
    report = json.loads(actual["outputs"]["research_report"])
    assert report["ablation_plan"] == selection and [
        run["metrics"]["accuracy"] for run in report["runs"]
    ] == [1.0, 0.0]
    assert all(task.run_count == 1 for task in repository.get_plan(actual["plan_id"]).nodes)
    assert not await sandbox.inventory()


async def test_repository_stage_uses_hash_verified_archive_and_only_runs_when_requested(
    legacy_docker, repository, monkeypatch
):
    client, sandbox = legacy_docker
    calls = []
    original = GitHubSource.fetch

    def transport(request):
        calls.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"sha": "a" * 40})
        assert request.url.path.endswith("a" * 40)
        return httpx.Response(200, content=make_repository_archive())

    async def controlled_source(self, spec):
        return await original(GitHubSource(self.settings, httpx.MockTransport(transport)), spec)

    monkeypatch.setattr(GitHubSource, "fetch", controlled_source)
    payload = request(
        "repo_prepare",
        {
            "research": {
                "repository": {"url": "https://github.com/example/controlled"},
                "entrypoint": "main.py",
                "max_repairs": 0,
            }
        },
    )
    payload["assigned_to"] = "coder_agent"
    async with asyncio.timeout(150):
        prepared = completed_result(await client.post("/api/execute", json=payload))
        manifest = json.loads(prepared["outputs"]["repo_manifest"])
        assert (
            manifest["commit"] == "a" * 40
            and manifest["files"][0]["sha256"] == hashlib.sha256(b"print(1)").hexdigest()
        )
        assert prepared["code"] == "print(1)" and prepared["outputs"]["code_file_path"] == "main.py"
        assert prepared["outputs"]["workspace_path"] == prepared["workflow_ref"]
        assert repository.get_plan(prepared["plan_id"]).status == Status.PENDING
        actual = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "paper_code_execute",
                    {"workflow_ref": prepared["workflow_ref"]},
                ),
            )
        )
    report = json.loads(actual["outputs"]["research_report"])
    assert (
        report["repository_commit"] == "a" * 40
        and report["runs"][0]["attempts"][-1]["stdout"] == "1\n"
    )
    assert len(calls) == 2 and not await sandbox.inventory()
