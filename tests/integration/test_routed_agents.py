import asyncio
import io
import json

import pytest
from fastapi.testclient import TestClient

from openresearch.agents.context import AgentContext
from openresearch.agents.router import RoutedAgents
from openresearch.api.app import create_app
from openresearch.errors import ContractError
from openresearch.models.context import Identity
from openresearch.models.execution import ExecutionLease
from openresearch.models.runtime import Status
from openresearch.models.submissions import PlanSubmission
from openresearch.planner.data import build_data_plan
from openresearch.research.data_profile import read_records
from openresearch.scheduler.worker import Worker
from openresearch.services.uploads import UploadService
from openresearch.storage.upload_files import UploadSubmission


async def test_data_agent_runs_uploaded_data_through_durable_worker_and_events(
    settings, repository, files, sandbox
) -> None:
    upload = UploadService(settings, repository).create(
        UploadSubmission(
            "measurements.csv", io.BytesIO(b"x,label\n1,a\n3,b\n,a\n"), Identity("a", "s")
        )
    )
    plan = build_data_plan(
        PlanSubmission("分析缺失值与均值", Identity("a", "s"), attachments=[upload.id]), settings
    )
    repository.save_plan(plan)
    repository.queue_plan(plan.id, "a")
    worker = Worker(settings, repository, files, sandbox)
    async with asyncio.timeout(5):
        while repository.get_plan(plan.id).status not in {Status.COMPLETED, Status.FAILED}:
            await worker.tick()
            await asyncio.sleep(0.01)
    finished = repository.get_plan(plan.id)
    assert finished.status == Status.COMPLETED, finished.nodes[0].error
    profile = json.loads(files.read(finished.artifacts["data_profile"]))
    assert profile["row_count"] == 3
    assert profile["columns"]["x"] == {
        "count": 3,
        "missing": 1,
        "min": 1,
        "max": 3,
        "mean": 2,
        "median": 2,
    }
    assert profile["source"]["sha256"] == upload.sha256
    assert "1/3" in files.read(finished.artifacts["data_chart"])
    assert any(event.event_type == "task_completed" for event in repository.list_events(plan.id))
    assert sandbox.created == []


async def test_role_router_rejects_cross_role_tool_execution(settings, repository, files, sandbox):
    agents = RoutedAgents(settings, repository, files, sandbox)
    assert set(agents.agents) == {"chat_agent", "librarian_agent", "data_agent", "coder_agent"}
    plan = build_data_plan(
        PlanSubmission("data", Identity("a", "s"), attachments=["a" * 32]), settings
    )
    task = plan.nodes[0]
    task.assigned_to = "chat_agent"
    lease = ExecutionLease(plan.id, task.id, "b" * 32, "worker")
    with pytest.raises(ContractError, match="cannot perform"):
        await agents.execute(AgentContext(plan, task, lease))


def test_data_plan_api_checks_upload_owner(settings, repository, sandbox):
    with TestClient(create_app(settings, repository, sandbox)) as client:
        uploaded = client.post(
            "/api/uploads",
            files={"file": ("data.csv", b"x\n1\n")},
            headers={"X-User-Id": "alice"},
        ).json()
        response = client.post(
            "/api/plan",
            json={"intent": "data", "workflow": "data", "attachments": [uploaded["id"]]},
            headers={"X-User-Id": "bob"},
        )
        assert response.status_code == 404


@pytest.mark.parametrize("raw", [b"a,a\n1,2", b"x,y\n1,2,3", b'[{"x":1},{}]', b"[]"])
def test_data_analysis_rejects_inconsistent_or_empty_records(raw):
    with pytest.raises(ContractError):
        read_records(raw, "data.json" if raw.startswith(b"[") else "data.csv", 20)
