import pytest
from pydantic import ValidationError

from openresearch.executor.engine import Engine
from openresearch.executor.models import DAG, ExecutorConfig, Node


@pytest.mark.parametrize(
    "nodes",
    [
        [],
        [{"id": "a"}, {"id": "a"}],
        [{"id": "a", "dependencies": ["missing"]}],
        [{"id": "a", "dependencies": ["a"]}],
        [{"id": "a", "dependencies": ["b"]}, {"id": "b", "dependencies": ["a"]}],
        [{"id": "a"}, {"id": "b", "dependencies": ["a", "a"]}],
        [{"id": " "}],
        [{"id": "a", "commands": [""]}],
        [{"id": "a", "commands": ["true", "true"]}],
        [{"id": "a", "commands": ["a", "b", "c", "d"]}],
        [{"id": "a", "commands": ["echo \x00"]}],
        [{"id": "a", "io_weight": 0}],
    ],
)
def test_invalid_dag_fails_before_execution(nodes):
    with pytest.raises(ValidationError):
        DAG(nodes=nodes)


def test_diamond_keeps_dependencies_and_commands_immutable():
    graph = DAG(
        nodes=[
            Node(id="root", commands=["true"]),
            Node(id="left", dependencies=["root"]),
            Node(id="right", dependencies=["root"]),
            Node(id="join", dependencies=["left", "right"]),
        ]
    )
    assert graph.nodes[-1].dependencies == ("left", "right")
    assert graph.nodes[0].commands == ("true",)
    with pytest.raises(ValidationError):
        graph.nodes[0].id = "changed"


@pytest.mark.parametrize("resources", [{"io_weight": 6}, {"gpu_weight": 1}])
async def test_impossible_resource_request_fails_before_starting_any_sandbox(resources):
    async with Engine(ExecutorConfig()) as engine:
        with pytest.raises(ValueError):
            await engine.execute_graph(
                DAG(nodes=[Node(id="invalid", commands=["true"], **resources)])
            )
        assert engine.last_receipt is None
