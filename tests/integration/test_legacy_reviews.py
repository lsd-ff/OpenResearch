import json

import pytest
from test_legacy_execution import legacy_client as legacy_client
from test_legacy_execution import request, result

from openresearch.models.runtime import Status


def review(kind, reference):
    return {
        "task_type": kind,
        "task_description": "复核已有真实结果，不执行新实验",
        "assigned_to": "data_agent",
        "inputs": {"workflow_ref": reference},
    }


async def test_code_review_and_plot_pin_original_results_without_executing_again(
    legacy_client, repository, sandbox
):
    actual = result(await legacy_client.post("/api/execute", json=request()))
    source = repository.get_plan(actual["plan_id"])
    source_json, containers = source.model_dump_json(), list(sandbox.created)
    verified = result(
        await legacy_client.post(
            "/api/execute", json=review("verify_result", actual["workflow_ref"])
        )
    )
    audit = json.loads(verified["outputs"]["audit_report"])
    assert audit["kind"] == "code" and audit["status"] == "verified"
    assert (
        verified["plan_id"] != source.id
        and verified["source_workflow_ref"] == actual["workflow_ref"]
    )
    derived = repository.get_plan(verified["plan_id"])
    assert derived.budget.max_task_attempts == 1 and derived.nodes[0].contract.allowed_tools == [
        "results.audit"
    ]
    plot = result(
        await legacy_client.post(
            "/api/execute", json=review("render_plot", verified["workflow_ref"])
        )
    )
    assert plot["image_base_64"].startswith("data:image/svg+xml;base64,")
    assert json.loads(plot["outputs"]["plot_spec"])["panels"]["program_attempts"] == [
        ["execution", 1.0]
    ]
    assert repository.get_plan(source.id).model_dump_json() == source_json
    assert sandbox.created == containers
    replay = result(
        await legacy_client.post(
            "/api/execute", json={"task_id": verified["task_id"], "plan_id": verified["plan_id"]}
        )
    )
    assert replay["outputs"] == verified["outputs"] and sandbox.created == containers


async def test_data_review_recalculates_statistics_from_owned_upload(legacy_client, sandbox):
    upload = await legacy_client.post(
        "/api/uploads", files={"file": ("data.csv", b"x,y\n1,yes\n2,\n", "text/csv")}
    )
    source_request = request("dataset_profile", {"upload_id": upload.json()["id"]})
    source_request["assigned_to"] = "data_agent"
    source = result(await legacy_client.post("/api/execute", json=source_request))
    actual = result(
        await legacy_client.post(
            "/api/execute", json=review("verify_result", source["workflow_ref"])
        )
    )
    audit = json.loads(actual["outputs"]["audit_report"])
    assert audit["status"] == "verified" and audit["sample_count"] == 2
    assert (
        audit["metrics"]["y"]["missing"] == 1.0
        and audit["dataset_sha256"] == upload.json()["sha256"]
    )
    assert not sandbox.created


@pytest.mark.parametrize("change", ["foreign", "raw_metrics", "role", "missing_ref"])
async def test_review_cannot_import_forged_results_or_cross_owners(legacy_client, change):
    source = result(await legacy_client.post("/api/execute", json=request()))
    payload = review("verify_result", source["workflow_ref"])
    headers = {}
    if change == "foreign":
        headers = {"X-User-Id": "bob"}
    elif change == "raw_metrics":
        payload["inputs"]["run_metrics"] = '{"exit_code":0,"stdout":"forged"}'
    elif change == "role":
        payload["assigned_to"] = "coder_agent"
    else:
        payload["inputs"] = {}
    response = await legacy_client.post("/api/execute", json=payload, headers=headers)
    assert response.status_code == (404 if change == "foreign" else 422), response.text


async def test_approval_pins_actual_artifact_and_detects_file_change_before_worker_reads(
    legacy_client, settings, repository, files, sandbox
):
    source = result(await legacy_client.post("/api/execute", json=request()))
    settings.require_approval = True
    pending = await legacy_client.post(
        "/api/execute", json=review("verify_result", source["workflow_ref"])
    )
    assert pending.status_code == 202 and len(sandbox.created) == 2
    selected = pending.json()
    original = repository.get_plan(source["plan_id"])
    artifact = original.artifacts["executed_code"]
    (files.root / artifact.location).write_text("changed after snapshot", encoding="utf-8")
    assert (
        await legacy_client.post(f"/api/plans/{selected['plan_id']}/approve")
    ).status_code == 200
    response = await legacy_client.post(
        "/api/execute", json={"plan_id": selected["plan_id"], "task_id": selected["task_id"]}
    )
    assert "event: error" in response.text and "event: result" not in response.text
    done = repository.get_plan(selected["plan_id"])
    audit = json.loads(files.read(done.artifacts["audit_report"]))
    assert done.status == Status.FAILED and not audit["integrity_valid"]
    assert repository.get_plan(source["plan_id"]).model_dump_json() == original.model_dump_json()
    assert len(sandbox.created) == 2
