import json

import httpx
import pytest
from pydantic import SecretStr

from openresearch.config import Settings
from openresearch.errors import ModelError
from openresearch.executor.agent import LLMAgent
from openresearch.llm.client import LLMClient


def planning_client(settings: Settings, responses: list[dict]):
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    settings.llm_schema_retries = 0
    received = []

    def provider(request):
        received.append(json.loads(request.content))
        content = json.dumps(responses[len(received) - 1])
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return LLMAgent(LLMClient(settings, httpx.MockTransport(provider))), received


async def test_executor_agent_preserves_go_plan_and_structured_alternatives(settings):
    agent, received = planning_client(
        settings,
        [
            {
                "nodes": [
                    {"id": "prepare", "instruction": "mkdir -p data", "dependencies": []},
                    {"id": "choose implementation", "instruction": "", "dependencies": ["prepare"]},
                ]
            },
            {"commands": ["printf first > data/value", "printf second > data/value"]},
        ],
    )
    graph = await agent.plan("prepare and choose an implementation")
    assert graph.nodes[0].commands == ("mkdir -p data",)
    assert graph.nodes[1].commands == () and graph.nodes[1].dependencies == ("prepare",)
    assert len(await agent.generate_strategies(graph.nodes[1].id)) == 2
    assert len(received) == 2
    assert json.loads(received[-1]["messages"][-1]["content"]) == {
        "instruction": "choose implementation"
    }


@pytest.mark.parametrize(
    "response",
    [
        {"nodes": [{"id": "a", "instruction": "true", "dependencies": ["missing"]}]},
        {"nodes": [{"id": "a", "instruction": "true", "dependencies": ["a"]}]},
    ],
)
async def test_executor_agent_rejects_invalid_model_graph_without_shell_fallback(
    settings, response
):
    agent, received = planning_client(settings, [response])
    with pytest.raises(ModelError):
        await agent.plan("goal")
    assert len(received) == 1


async def test_executor_agent_rejects_empty_alternatives_without_shell_fallback(settings):
    agent, received = planning_client(settings, [{"commands": []}])
    with pytest.raises(ModelError):
        await agent.generate_strategies("make a file")
    assert len(received) == 1
