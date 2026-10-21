import json

import httpx
import pytest
from pydantic import SecretStr

from openresearch.agents.chat import ChatAgent
from openresearch.agents.context import AgentContext
from openresearch.llm.client import LLMClient
from openresearch.models.execution import ExecutionLease
from openresearch.models.research import (
    EnvironmentReport,
    FilePatch,
    ResearchPatch,
    ResearchReport,
    ResearchRun,
    RunAttempt,
    SourceIntegrity,
)
from openresearch.models.runtime import Edge, Plan, Task, TaskContract


@pytest.mark.parametrize("failed_attempts", [0, 2])
async def test_summary_sees_execution_after_a_long_patch_and_does_not_request_pdf_citations(
    settings, repository, files, failed_attempts
):
    settings.llm_model = "test"
    settings.llm_api_key = SecretStr("test-only")
    patch = ResearchPatch(files=[FilePatch(path="main.py", content="x = 'SOURCE_ONLY'\n" * 1000)])
    report = ResearchReport(
        repository_commit=None,
        success=True,
        source_integrity=SourceIntegrity(
            provided_code_sha256="d" * 64, prepared_entrypoint_sha256="d" * 64
        ),
        runs=[
            ResearchRun(
                variant="baseline",
                arguments=[],
                environment=EnvironmentReport(
                    python="3.12", platform="linux", installed={}, container_image="test"
                ),
                attempts=[
                    RunAttempt(
                        number=number + 1,
                        patch_sha256="b" * 64,
                        patch=patch,
                        exit_code=1,
                        stdout="",
                        stderr="ACTUAL_FAILURE_" + str(number),
                        error="experiment exited with status 1",
                    )
                    for number in range(failed_attempts)
                ]
                + [
                    RunAttempt(
                        number=failed_attempts + 1,
                        patch_sha256="a" * 64,
                        patch=patch,
                        exit_code=0,
                        stdout="ACTUAL_LOSS=0.034155",
                        stderr="",
                    )
                ],
            )
        ],
    )
    task = Task(
        name="summarize",
        type="chat_answer",
        assigned_to="chat_agent",
        required_artifacts=["research_report", "final_patch", "experiment_design"],
        trigger_rule="all_done",
        contract=TaskContract(
            allowed_tools=["chat.answer"],
            input_artifacts=["research_report", "final_patch", "experiment_design"],
        ),
    )
    producer = Task(
        name="actual experiment",
        type="research_execute",
        assigned_to="coder_agent",
        output_artifacts=["research_report", "final_patch"],
        contract=TaskContract(output_artifacts=["research_report", "final_patch"]),
    )
    design = Task(
        name="planning proposal",
        type="research_design",
        assigned_to="coder_agent",
        result="PLANNING_ONLY_REMOVE_ASSERTION",
        output_artifacts=["experiment_design"],
        contract=TaskContract(output_artifacts=["experiment_design"]),
    )
    producer.dependencies = [design.id]
    task.dependencies = [producer.id, design.id]
    plan = Plan(
        user_intent="report actual loss",
        owner_id="alice",
        session_id="s",
        nodes=[design, producer, task],
        edges=[
            Edge(**{"from": parent, "to": node.id})
            for node in (producer, task)
            for parent in node.dependencies
        ],
    )
    lease = ExecutionLease(plan.id, task.id, "b" * 32, "worker")
    for key, content in {
        "research_report": report.model_dump_json(),
        "final_patch": patch.model_dump_json(),
        "experiment_design": json.dumps({"rationale": "PLANNING_ONLY_REMOVE_ASSERTION"}),
        "claim_verification_report": "# Frozen functional checks\nFUNCTIONAL_CHECK_VERIFIED\n",
        "benchmark_predictions": "[]",
    }.items():
        plan.artifacts[key] = files.write(lease, key, content)

    def provider(request):
        payload = json.loads(request.content)
        text = json.dumps(payload["messages"])
        assert "ACTUAL_LOSS=0.034155" in text and "SOURCE_ONLY" not in text
        assert "PLANNING_ONLY_REMOVE_ASSERTION" not in text
        assert "citations=[]" in text and "SERVER_CONTEXT_DATA" not in text
        artifacts = json.loads(payload["messages"][-1]["content"])["artifacts"]
        assert "FUNCTIONAL_CHECK_VERIFIED" in artifacts["claim_verification_report"]
        available = json.loads(artifacts["available_artifacts"])
        assert "benchmark_predictions" in available and "claim_verification_report" in available
        assert "benchmark_predictions" not in artifacts
        assert all("result" not in item for item in json.loads(artifacts["task_outcomes"]))
        assert json.loads(artifacts["source_integrity"])["provided_code_preserved"] is True
        history = json.loads(artifacts["execution_history"])[0]["attempts"]
        assert [item["exit_code"] for item in history] == [1] * failed_attempts + [0]
        assert all(
            "ACTUAL_FAILURE_" + str(index) in history[index]["stderr_tail"]
            for index in range(failed_attempts)
        )
        assert "ACTUAL_FAILURE" not in artifacts["research_report"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "response": "Measured loss 0.034155; no generalization test.",
                                    "citations": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    result = await ChatAgent(
        LLMClient(settings, httpx.MockTransport(provider)), repository, files
    ).execute(AgentContext(plan, task, lease))
    assert "0.034155" in result.result
