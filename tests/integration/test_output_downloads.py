from fastapi.testclient import TestClient

from openresearch.api.app import create_app
from openresearch.models.execution import ExecutionLease, TaskCompletion
from openresearch.models.output_files import OutputFile, OutputFiles
from openresearch.models.runtime import Plan, Task, TaskContract, TaskResult


def test_output_download_checks_owner_and_content_integrity(settings, repository, files, sandbox):
    import base64
    import hashlib

    raw = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    bundle = OutputFiles(
        files=[
            OutputFile(
                path="plot.svg",
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                base64=base64.b64encode(raw).decode(),
            )
        ]
    )
    task = Task(
        name="collected output",
        type="code_execute",
        assigned_to="coder_agent",
        output_artifacts=["execution_files"],
        contract=TaskContract(output_artifacts=["execution_files"]),
    )
    plan = Plan(user_intent="download", owner_id="alice", session_id="s", nodes=[task])
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "alice")
    claimed = repository.claim_next("worker", 30, 1)
    lease = ExecutionLease.for_task(*claimed, "worker")
    result = TaskResult(outputs={"execution_files": bundle.model_dump_json()})
    artifact = files.write(lease, "execution_files", bundle.model_dump_json())
    repository.finish(lease, TaskCompletion(result, [artifact]))
    location = f"/api/plans/{plan.id}/artifacts/{artifact.id}/files/0"
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.get(location, headers={"X-User-Id": "alice"})
        assert response.status_code == 200 and response.content == raw
        assert response.headers["Content-Type"].startswith("image/svg+xml")
        assert "attachment" in response.headers["Content-Disposition"]
        assert "sandbox" in response.headers["Content-Security-Policy"]
        assert client.get(location, headers={"X-User-Id": "bob"}).status_code == 404
        assert client.get(location[:-1] + "7", headers={"X-User-Id": "alice"}).status_code == 404
