import json

import pytest
from pydantic import ValidationError

from openresearch.models.execution import ExecutionLease, TaskCompletion
from openresearch.models.runtime import Plan, Status, Task, TaskContract, TaskResult


def reporting_plan():
    work = Task(name="Actual work", type="code_execute", assigned_to="coder_agent")
    dependent = Task(
        name="Dependent work",
        type="code_execute",
        assigned_to="coder_agent",
        dependencies=[work.id],
    )
    report = Task(
        name="Summarize terminal results",
        type="chat_answer",
        assigned_to="chat_agent",
        trigger_rule="all_done",
        dependencies=[work.id, dependent.id],
        output_artifacts=["chat_answer"],
        contract=TaskContract(output_artifacts=["chat_answer"], allowed_tools=["chat.answer"]),
    )
    return Plan(
        user_intent="report actual failure",
        owner_id="alice",
        session_id="failure",
        nodes=[work, dependent, report],
    )


def fail_upstream(repository, plan):
    repository.save_plan(plan)
    repository.queue_plan(plan.id, plan.owner_id)
    claimed, task = repository.claim_next("worker", 30, 2)
    lease = ExecutionLease.for_task(claimed, task, "worker")
    repository.finish(lease, TaskCompletion(error="actual source execution failed"))


def test_failed_work_can_produce_report_without_becoming_success(repository, files):
    plan = reporting_plan()
    fail_upstream(repository, plan)
    pending = repository.get_plan(plan.id)
    assert [node.status for node in pending.nodes] == [Status.FAILED, Status.BLOCKED, Status.READY]
    assert pending.status == Status.IN_PROGRESS
    claimed, report = repository.claim_next("reporter", 30, 2)
    lease = ExecutionLease.for_task(claimed, report, "reporter")
    content = json.dumps({"response": "The actual source execution failed", "citations": []})
    artifact = files.write(lease, "chat_answer", content)
    repository.finish(
        lease,
        TaskCompletion(
            TaskResult(result="Failure reported", outputs={"chat_answer": content}),
            [artifact],
        ),
    )
    done = repository.get_plan(plan.id)
    assert done.status == Status.FAILED and done.nodes[-1].status == Status.COMPLETED
    assert done.nodes[0].error == "actual source execution failed"
    assert files.read(done.artifacts["chat_answer"]) == content


def test_user_cancel_stops_pending_failure_report(repository):
    plan = reporting_plan()
    fail_upstream(repository, plan)
    repository.cancel(plan.id, plan.owner_id)
    assert repository.claim_next("reporter", 30, 2) is None
    assert repository.get_plan(plan.id).nodes[-1].status == Status.CANCELED


def test_failure_trigger_cannot_grant_sandbox_execution():
    plan = reporting_plan()
    data = plan.model_dump()
    data["nodes"][-1]["contract"]["allowed_tools"] = ["sandbox.python"]
    with pytest.raises(ValidationError, match="read-only"):
        Plan.model_validate(data)


def test_planner_cannot_start_failure_summary_before_its_parent_branches_finish(settings):
    from openresearch.errors import ContractError
    from openresearch.models.blueprint import PlanBlueprint
    from openresearch.planner.materialize import materialize

    plan = reporting_plan()
    refs = {task.id: f"stage_{index}" for index, task in enumerate(plan.nodes)}
    proposal = PlanBlueprint(
        strategy="report stages",
        nodes=[
            {
                "ref": refs[task.id],
                "type": task.type,
                "template_task_id": task.id,
                "assigned_to": task.assigned_to,
                "name": task.name,
                "description": "Use actual results",
                "dependencies": [refs[parent] for parent in task.dependencies]
                if task.trigger_rule == "all_success"
                else [],
            }
            for task in plan.nodes
        ],
    )
    with pytest.raises(ContractError, match="completion barrier"):
        materialize(proposal, plan, settings)


async def test_report_model_receives_actual_failed_and_blocked_task_states(
    settings,
    repository,
    files,
):
    import httpx
    from pydantic import SecretStr

    from openresearch.agents.chat import ChatAgent
    from openresearch.agents.context import AgentContext
    from openresearch.llm.client import LLMClient

    plan = reporting_plan()
    fail_upstream(repository, plan)
    claimed, report = repository.claim_next("reporter", 30, 2)
    settings.llm_api_key = SecretStr("test-only")
    settings.llm_model = "test-model"

    def provider(request):
        data = json.loads(request.content)
        context = json.loads(data["messages"][-1]["content"])
        outcomes = json.loads(context["artifacts"]["task_outcomes"])
        assert [node["status"] for node in outcomes] == ["failed", "blocked"]
        assert outcomes[0]["error"] == "actual source execution failed"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "response": "实际执行失败；下游没有运行。",
                                    "citations": [],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    agent = ChatAgent(LLMClient(settings, httpx.MockTransport(provider)), repository, files)
    result = await agent.execute(
        AgentContext(
            claimed,
            report,
            ExecutionLease.for_task(claimed, report, "reporter"),
        )
    )
    assert "实际执行失败" in result.result
