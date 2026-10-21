import pytest
from pydantic import ValidationError

from openresearch.executor.engine import Engine
from openresearch.executor.gates import Gatekeeper, ResourceConfig
from openresearch.executor.models import DAG, ExecutorConfig, Node
from openresearch.executor.resources import effective_graph
from openresearch.sandbox.core.models import CoreConfig


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


@pytest.mark.parametrize("capacity,weight", [(0, 0), (0, 1), (1, 2)])
async def test_gpu_enabled_graph_requires_sufficient_effective_capacity(capacity, weight):
    config = ExecutorConfig(sandbox=CoreConfig(gpu_enabled=True))
    gates = Gatekeeper(ResourceConfig(max_gpu_tasks=capacity))
    async with Engine(config, gates=gates) as engine:
        with pytest.raises(ValueError, match="GPU weight"):
            await engine.execute_graph(
                DAG(nodes=[Node(id="gpu", commands=["true"], gpu_weight=weight)])
            )
        assert engine.last_receipt is None and gates.gpu_usage.in_use == 0


def test_effective_gpu_weights_preserve_the_original_graph_and_explicit_budget():
    original = DAG(nodes=[Node(id="automatic"), Node(id="explicit", gpu_weight=2)])
    gates = Gatekeeper(ResourceConfig(max_gpu_tasks=2))
    config = ExecutorConfig(sandbox=CoreConfig(gpu_enabled=True))
    resolved = effective_graph(original, config, gates)
    assert [node.gpu_weight for node in original.nodes] == [0, 2]
    assert [node.gpu_weight for node in resolved.nodes] == [1, 2]
