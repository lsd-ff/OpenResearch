from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openresearch.executor.gates import ResourceConfig
from openresearch.sandbox.core.models import CoreConfig


def valid_commands(commands: tuple[str, ...]) -> tuple[str, ...]:
    if len(set(commands)) != len(commands):
        raise ValueError("candidate commands must be unique")
    if any(
        not command.strip() or "\x00" in command or len(command.encode()) > 65536
        for command in commands
    ):
        raise ValueError("invalid candidate command")
    return commands


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=200)
    commands: tuple[str, ...] = Field(default=(), max_length=3)
    dependencies: tuple[str, ...] = Field(default=(), max_length=64)
    io_weight: int = Field(default=1, ge=1, le=4096, strict=True)
    gpu_weight: int = Field(default=0, ge=0, le=4096, strict=True)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("invalid node id")
        return value

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return valid_commands(value)


class DAG(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nodes: tuple[Node, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_graph(self) -> "DAG":
        remaining = {node.id: set(node.dependencies) for node in self.nodes}
        if len(remaining) != len(self.nodes):
            raise ValueError("node ids must be unique")
        for node in self.nodes:
            if len(set(node.dependencies)) != len(node.dependencies):
                raise ValueError("node dependencies must be unique")
            if set(node.dependencies) - remaining.keys():
                raise ValueError("unknown node dependency")
        while remaining:
            ready = {key for key, dependencies in remaining.items() if not dependencies}
            if not ready:
                raise ValueError("executor graph contains a cycle")
            remaining = {key: deps - ready for key, deps in remaining.items() if key not in ready}
        return self


class ExecutorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sandbox: CoreConfig = Field(default_factory=CoreConfig)
    resources: ResourceConfig = Field(default_factory=ResourceConfig)
    max_parallel_nodes: int = Field(default=5, ge=1, le=64, strict=True)
    node_timeout_seconds: float = Field(default=900, gt=0, le=3600, allow_inf_nan=False)
    graph_timeout_seconds: float = Field(default=1800, gt=0, le=7200, allow_inf_nan=False)
    max_attempts_per_node: int = Field(default=192, ge=1, le=4096, strict=True)
    max_attempts: int = Field(default=256, ge=1, le=4096, strict=True)
