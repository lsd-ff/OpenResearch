import json

import pytest
from conftest import FakeSandbox
from fastapi.testclient import TestClient

from openresearch.agents.code_repair import CodeRepairer
from openresearch.api.app import create_app
from openresearch.models.recovery import RecoveryProposal
from openresearch.models.runtime import ExecutionResult, Status
from openresearch.planner.code import build_code_plan
from openresearch.scheduler.worker import Worker


class FailedThenActualFixture(FakeSandbox):
    """仅验证恢复状态机的离线替身；真实执行另有 Docker 验收。"""

    def __init__(self):
        super().__init__()
        self.executions = 0
        self.always_fail = False
        self.error = "AttributeError: module 'json' has no attribute 'decode'"

    async def execute(self, sandbox_id, code, on_log):
        if code.startswith("# OpenResearch artifact collector\n"):
            return ExecutionResult(
                stdout='{"version":"execution.files/v1","files":[]}', exit_code=0
            )
        self.executions += 1
        return (
            ExecutionResult(exit_code=1, stderr=self.error)
            if self.executions == 1 or self.always_fail
            else ExecutionResult(stdout="real fixture second result", exit_code=0)
        )


async def test_runtime_repair_uses_new_container_and_retains_original_failure(
    settings, repository, files, monkeypatch
):
    code = "import json\nvalue = json.decode('{}')\nassert value == {}\nprint(value)"
    plan = build_code_plan("执行 JSON 测试并修复错误", "alice", "repair", settings, code)
    plan.nodes[1].description = "Untrusted planner description must not replace the human goal."
    repository.save_plan(plan)
    sandbox = FailedThenActualFixture()

    async def propose(self, goal, original, error, requirements, allow_dependencies):
        assert goal == "执行 JSON 测试并修复错误"
        assert "AttributeError" in error and original == code
        return RecoveryProposal(
            action="code",
            reason="Use the actual JSON API.",
            code=original.replace("json.decode", "json.loads"),
        )

    monkeypatch.setattr(CodeRepairer, "propose", propose)
    worker = Worker(settings, repository, files, sandbox)
    repository.queue_plan(plan.id, "alice")
    try:
        import asyncio

        async with asyncio.timeout(10):
            while repository.get_plan(plan.id).status not in {Status.COMPLETED, Status.FAILED}:
                await worker.tick()
                await asyncio.sleep(0.01)
        done = repository.get_plan(plan.id)
        assert done.status == Status.COMPLETED, [node.error for node in done.nodes]
        assert len(sandbox.created) == 2 and sorted(sandbox.closed) == sorted(sandbox.created)
        report = json.loads(files.read(done.artifacts["code_recovery_report"]))
        assert len(report["attempts"]) == 2
        assert report["attempts"][0]["output"]["exit_code"] == 1
        assert report["attempts"][0]["error"]
        assert report["attempts"][1]["output"]["exit_code"] == 0
        assert "json.loads" in files.read(done.artifacts["executed_code"])
    finally:
        await worker._shutdown()


@pytest.mark.parametrize("mode", ["exhausted", "rejected", "disabled", "assertion"])
async def test_failed_recovery_never_changes_failure_to_success_or_exceeds_budget(
    settings, repository, files, monkeypatch, mode
):
    import asyncio

    settings.code_max_repairs = 0 if mode == "disabled" else 2
    code = "import json\nvalue = json.decode('{}')\nassert value == {}\n"
    plan = build_code_plan("bounded repair", "alice", mode, settings, code)
    repository.save_plan(plan)
    sandbox = FailedThenActualFixture()
    sandbox.always_fail = True
    if mode == "assertion":
        sandbox.error = "AssertionError: actual independent check failed"
    calls = []

    async def propose(self, goal, original, error, requirements, allow_dependencies):
        calls.append(error)
        return RecoveryProposal(
            action="code",
            reason="Controlled proposal for recovery limits.",
            code="print('pretend passed')"
            if mode == "rejected"
            else original + f"# {len(calls)}\n",
        )

    monkeypatch.setattr(CodeRepairer, "propose", propose)
    worker = Worker(settings, repository, files, sandbox)
    repository.queue_plan(plan.id, "alice")
    try:
        async with asyncio.timeout(10):
            while repository.get_plan(plan.id).status not in {Status.COMPLETED, Status.FAILED}:
                await worker.tick()
                await asyncio.sleep(0.01)
        done = repository.get_plan(plan.id)
        assert done.status == Status.FAILED
        report = json.loads(files.read(done.artifacts["code_recovery_report"]))
        assert not report["success"]
        assert len(report["attempts"]) == (3 if mode == "exhausted" else 1)
        assert len(calls) == (2 if mode == "exhausted" else 1 if mode == "rejected" else 0)
        if mode == "rejected":
            assert report["decisions"][0]["accepted"] is False
            assert report["decisions"][0]["proposal"]["code"] == "print('pretend passed')"
            assert "preserve" in report["decisions"][0]["error"]
        assert sorted(sandbox.closed) == sorted(sandbox.created)
    finally:
        await worker._shutdown()


@pytest.mark.parametrize("reason", ["stdlib", "permission"])
async def test_model_cannot_install_a_stdlib_substitute_or_bypass_dependency_permission(
    settings, repository, files, monkeypatch, reason
):
    import asyncio

    plan = build_code_plan("permission check", "alice", reason, settings, "print(1)")
    if reason == "permission":
        plan.nodes[1].contract.allowed_tools.remove("environment.lock")
    repository.save_plan(plan)
    sandbox = FailedThenActualFixture()
    sandbox.error = (
        "ModuleNotFoundError: No module named 'json'"
        if reason == "stdlib"
        else "ModuleNotFoundError: No module named 'yaml'"
    )

    async def propose(self, goal, original, error, requirements, allow_dependencies):
        assert not allow_dependencies
        return RecoveryProposal(
            action="dependencies", reason="Forbidden fixture suggestion.", requirements=["PyYAML"]
        )

    monkeypatch.setattr(CodeRepairer, "propose", propose)
    worker = Worker(settings, repository, files, sandbox)
    repository.queue_plan(plan.id, "alice")
    try:
        async with asyncio.timeout(10):
            while repository.get_plan(plan.id).status not in {Status.COMPLETED, Status.FAILED}:
                await worker.tick()
                await asyncio.sleep(0.01)
        done = repository.get_plan(plan.id)
        assert done.status == Status.FAILED and sandbox.executions == 1
        report = json.loads(files.read(done.artifacts["code_recovery_report"]))
        assert "not authorized" in report["decisions"][0]["error"]
    finally:
        await worker._shutdown()


@pytest.mark.parametrize(
    ("instruction", "approval", "status"),
    [
        ("直接执行以下代码并修复错误", False, "ready"),
        ("不要直接执行以下代码", False, "pending"),
        ("直接执行以下代码并修复错误", True, "awaiting_approval"),
    ],
)
def test_explicit_chat_execution_respects_planning_only_and_approval(
    settings, repository, sandbox, instruction, approval, status
):
    settings.require_approval = approval
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/messages", json={"message": instruction + "\n```python\nprint(7)\n```"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["plan_graph"]["status"] == status


def test_source_text_cannot_trigger_unrequested_execution(settings, repository, sandbox):
    with TestClient(create_app(settings, repository, sandbox)) as client:
        response = client.post(
            "/api/messages",
            json={"message": '查看这段代码\n```python\nprint("直接执行以下代码")\n```'},
        )
        assert response.status_code == 200, response.text
        assert response.json()["plan_graph"]["status"] == "pending"
