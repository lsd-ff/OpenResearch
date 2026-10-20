import asyncio
import base64
import json
from xml.etree import ElementTree

from test_docker_smoke import pytestmark as pytestmark
from test_legacy_api import completed_result, payload
from test_legacy_api import legacy_docker as legacy_docker
from test_legacy_stage_runs import fresh_benchmark, request
from test_research import PREDICTOR

from openresearch.models.runtime import Status


def review(kind, reference):
    return {
        "task_type": kind,
        "task_description": "复核已有真实数据和冻结主张，不执行新实验",
        "assigned_to": "research_coding_agent" if kind == "benchmark_validate" else "data_agent",
        "inputs": {"workflow_ref": reference},
    }


async def test_all_six_review_operations_use_actual_saved_predictions_without_new_containers(
    legacy_docker, repository, monkeypatch
):
    client, sandbox = legacy_docker
    created = []
    create = sandbox.create

    async def record(plan_id, execution_id):
        identifier = await create(plan_id, execution_id)
        created.append(identifier)
        return identifier

    monkeypatch.setattr(sandbox, "create", record)
    async with asyncio.timeout(180):
        source = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "benchmark_execute",
                    await fresh_benchmark(client, PREDICTOR),
                ),
            )
        )
        root = repository.get_plan(source["plan_id"])
        source_json, initial_containers = root.model_dump_json(), list(created)
        current = source["workflow_ref"]
        for operation in [
            "benchmark_validate",
            "verify_result",
            "paper_compare",
            "claim_evidence_build",
            "result_visualization",
            "render_plot",
        ]:
            actual = completed_result(
                await client.post("/api/execute", json=review(operation, current))
            )
            audit = json.loads(actual["outputs"]["audit_report"])
            assert audit["status"] == "verified" and audit["integrity_valid"]
            assert audit["sample_count"] == 12 and audit["metrics"] == {
                "baseline": {"accuracy": 1.0}
            }
            assert actual["source_workflow_ref"] == source["workflow_ref"]
            assert actual["plan_id"] != root.id
            derived = repository.get_plan(actual["plan_id"])
            assert derived.nodes[0].run_count == 1 and derived.budget.max_task_attempts == 1
            if operation in {"paper_compare", "claim_evidence_build"}:
                graph = json.loads(actual["outputs"]["claim_evidence_graph"])
                assert graph["claims"][0]["criteria"][0]["status"] == "verified"
            if operation in {"result_visualization", "render_plot"}:
                raw = base64.b64decode(actual["image_base_64"].split(",", 1)[1])
                assert raw.decode() == actual["outputs"]["result_plot"]
                svg = ElementTree.fromstring(raw)
                assert (
                    len(
                        svg.findall(
                            "{http://www.w3.org/2000/svg}g/{http://www.w3.org/2000/svg}rect"
                        )
                    )
                    == 1
                )
                assert json.loads(actual["outputs"]["plot_spec"])["panels"] == {
                    "accuracy": [["baseline", 1.0]]
                }
            current = actual["workflow_ref"]
            assert (
                created == initial_containers
                and repository.get_plan(root.id).model_dump_json() == source_json
            )
    assert len(initial_containers) == 3 and not await sandbox.inventory()


async def test_code_validation_retains_actual_failed_assertion_and_does_not_rerun(
    legacy_docker, repository, files
):
    client, sandbox = legacy_docker
    async with asyncio.timeout(90):
        response = await client.post(
            "/api/execute", json=payload("execute_code", {"code": "assert sum([2,3]) == 99\n"})
        )
        assert "event: error" in response.text
        root = repository.get_plan(response.headers["x-openresearch-plan-id"])
        assert root.status == Status.FAILED
        original = root.model_dump_json()
        failed = await client.post(
            "/api/execute", json=review("verify_result", "openresearch:" + root.id)
        )
        assert "event: error" in failed.text and "event: result" not in failed.text
        derived = repository.get_plan(failed.headers["x-openresearch-plan-id"])
        audit = json.loads(files.read(derived.artifacts["audit_report"]))
        assert derived.status == Status.FAILED and audit["integrity_valid"]
        assert (
            not audit["execution_success"] and audit["metrics"]["execution"]["final_exit_code"] != 0
        )
        assert repository.get_plan(root.id).model_dump_json() == original
    assert not await sandbox.inventory()


async def test_comparison_of_failed_scientific_execution_never_verifies_claims(
    legacy_docker, repository
):
    client, sandbox = legacy_docker
    specification = {
        "max_repairs": 0,
        "rubric": {
            "claims": [
                {
                    "title": "A real result is required",
                    "statement": "A runnable implementation must produce real evidence",
                    "criteria": [
                        {
                            "description": "real algorithm behavior",
                            "required_evidence": ["research_report"],
                        }
                    ],
                }
            ]
        },
    }
    async with asyncio.timeout(120):
        failed = await client.post(
            "/api/execute",
            json=request(
                "paper_code_execute",
                {
                    "code": "raise RuntimeError('ACTUAL_SOURCE_FAILURE')\n",
                    "research": specification,
                },
            ),
        )
        root_id = failed.headers["x-openresearch-plan-id"]
        assert repository.get_plan(root_id).status == Status.FAILED
        original = repository.get_plan(root_id).model_dump_json()
        compared = completed_result(
            await client.post(
                "/api/execute", json=review("paper_compare", "openresearch:" + root_id)
            )
        )
        audit = json.loads(compared["outputs"]["audit_report"])
        graph = json.loads(compared["outputs"]["claim_evidence_graph"])
        assert (
            audit["integrity_valid"]
            and audit["status"] == "failed"
            and not audit["execution_success"]
        )
        assert graph["claims"][0]["criteria"][0]["status"] == "unverifiable"
        assert (
            "ACTUAL_SOURCE_FAILURE" not in compared["outputs"]["comparison_report"]
            or "failed" in compared["outputs"]["comparison_report"]
        )
        assert repository.get_plan(root_id).model_dump_json() == original
    assert not await sandbox.inventory()
