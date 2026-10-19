import json

import httpx
import pytest
from pydantic import SecretStr

from openresearch.llm.client import LLMClient
from openresearch.models.blueprint import PlanBlueprint
from openresearch.planner.agent import PlannerAgent
from openresearch.planner.code import build_code_plan


def blueprint() -> dict:
    return {
        "strategy": "Prepare and execute code, independently discover references, then summarize.",
        "nodes": [
            {
                "ref": "prepare",
                "type": "code_generate",
                "name": "Prepare actual code",
                "assigned_to": "coder_agent",
                "description": "Prepare the requested code.",
            },
            {
                "ref": "run",
                "type": "code_execute",
                "name": "Run in sandbox",
                "assigned_to": "coder_agent",
                "description": "Run the actual prepared code.",
                "dependencies": ["prepare"],
            },
            {
                "ref": "sources",
                "type": "source_discover",
                "name": "Find supporting papers",
                "assigned_to": "librarian_agent",
                "description": "Search real references.",
                "query": "Transformer",
            },
            {
                "ref": "answer",
                "type": "chat_answer",
                "name": "Interpret actual outputs",
                "assigned_to": "chat_agent",
                "description": "Summarize actual artifacts.",
                "dependencies": ["run", "sources"],
            },
        ],
    }


def planner(settings, repository, payload):
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("test-key")
    settings.llm_schema_retries = 0
    agent = PlannerAgent(settings, repository)

    def respond(request):
        content = json.loads(request.content)
        assert "required_stages" in content["messages"][-1]["content"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
        )

    agent.llm = LLMClient(settings, httpx.MockTransport(respond))
    return agent


async def test_llm_creates_a_valid_parallel_dag_without_changing_server_code_or_permissions(
    settings, repository
):
    template = build_code_plan("explicit test", "a", "s", settings, "print(42)")
    before = template.model_dump_json()
    result = await planner(settings, repository, blueprint()).refine(template)
    assert result.planning.strategy == "llm"
    assert len(result.nodes) == 4
    prepare, run, sources, answer = result.nodes
    assert prepare.inputs == {"code": "print(42)"}
    assert run.contract.allowed_tools == ["sandbox.python"]
    assert run.dependencies == [prepare.id] and sources.dependencies == []
    assert answer.dependencies == [run.id, sources.id]
    assert answer.required_artifacts == ["run_metrics", "discovered_sources"]
    assert template.model_dump_json() == before
    assert result.id == template.id and result.owner_id == "a"


@pytest.mark.parametrize(
    "fault", ["cycle", "missing_stage", "missing_input", "role", "unknown_ref", "duplicate"]
)
async def test_invalid_model_dags_fall_back_without_mutating_validated_template(
    settings, repository, fault
):
    payload = blueprint()
    if fault == "cycle":
        payload["nodes"][0]["dependencies"] = ["run"]
    elif fault == "missing_stage":
        payload["nodes"] = payload["nodes"][2:]
    elif fault == "missing_input":
        payload["nodes"][1]["dependencies"] = []
    elif fault == "role":
        payload["nodes"][1]["assigned_to"] = "chat_agent"
    elif fault == "unknown_ref":
        payload["nodes"][1]["dependencies"] = ["not_present"]
    elif fault == "duplicate":
        payload["nodes"].append(payload["nodes"][0])
    template = build_code_plan("explicit", "a", "s", settings, "print(42)")
    original_nodes = [task.model_dump_json() for task in template.nodes]
    result = await planner(settings, repository, payload).refine(template)
    assert result.planning.strategy == "template"
    assert result.planning.reason == "model_or_graph_validation_failed"
    assert [task.model_dump_json() for task in result.nodes] == original_nodes


async def test_unconfigured_model_keeps_executable_template(settings, repository):
    result = await PlannerAgent(settings, repository).refine(
        build_code_plan("smoke", "a", "s", settings)
    )
    assert result.planning.reason == "model_not_configured"
    assert len(result.nodes) == 2


async def test_model_cannot_add_another_users_upload_to_data_task(settings, repository):
    payload = blueprint()
    payload["nodes"].append(
        {
            "ref": "data",
            "type": "data_analyze",
            "name": "Analyze dataset",
            "assigned_to": "data_agent",
            "description": "Profile records.",
            "upload_id": "f" * 32,
        }
    )
    template = build_code_plan("smoke", "a", "s", settings)
    result = await planner(settings, repository, payload).refine(template)
    assert result.planning.strategy == "template"


def test_blueprint_contract_does_not_accept_model_provided_tools_or_code():
    payload = blueprint()
    payload["nodes"][0]["allowed_tools"] = ["host.exec"]
    with pytest.raises(ValueError):
        PlanBlueprint.model_validate(payload)
