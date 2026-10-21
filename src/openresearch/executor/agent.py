import json
from typing import Protocol

from pydantic import Field, model_validator

from openresearch.executor.models import DAG, Node, valid_commands
from openresearch.llm.client import LLMClient
from openresearch.models.runtime import ContractModel


class PlanningAgent(Protocol):
    async def plan(self, goal: str) -> DAG: ...

    async def generate_strategies(self, instruction: str) -> tuple[str, ...]: ...


class PlanNode(ContractModel):
    id: str = Field(min_length=1, max_length=200)
    instruction: str = Field(default="", max_length=65536)
    dependencies: tuple[str, ...] = Field(default=(), max_length=64)


class PlanProposal(ContractModel):
    nodes: tuple[PlanNode, ...] = Field(min_length=1, max_length=64)

    def graph(self) -> DAG:
        return DAG(
            nodes=tuple(
                Node(
                    id=node.id,
                    commands=(node.instruction,) if node.instruction.strip() else (),
                    dependencies=node.dependencies,
                )
                for node in self.nodes
            )
        )

    @model_validator(mode="after")
    def validate_graph(self) -> "PlanProposal":
        self.graph()
        return self


class StrategyProposal(ContractModel):
    commands: tuple[str, ...] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def validate_commands(self) -> "StrategyProposal":
        valid_commands(self.commands)
        return self


class LLMAgent:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def plan(self, goal: str) -> DAG:
        if not goal.strip() or len(goal) > 16000:
            raise ValueError("invalid executor goal")
        proposal = await self.client.request_typed(
            [
                {
                    "role": "system",
                    "content": (
                        "将目标解析为 Linux 容器内的任务 DAG。只返回 JSON nodes，"
                        "每项包含 id、instruction、dependencies。相互独立任务只依赖必要上游。"
                        "instruction 非空时是一条完整 Bash 命令；需要竞速时留空，"
                        "并在 id 中明确任务目标。最多64节点，不得循环或引用不存在节点。"
                        "候选只在隔离容器运行，不能假设宿主资源或未声明依赖。不要声称已经执行。"
                    ),
                },
                {"role": "user", "content": json.dumps({"goal": goal}, ensure_ascii=False)},
            ],
            PlanProposal,
        )
        return proposal.graph()

    async def generate_strategies(self, instruction: str) -> tuple[str, ...]:
        if not instruction.strip() or len(instruction) > 8000:
            raise ValueError("invalid strategy instruction")
        proposal = await self.client.request_typed(
            [
                {
                    "role": "system",
                    "content": (
                        "为目标提供2到3个不同的完整 Linux Bash 候选命令，"
                        "返回 JSON commands 字符串数组。每项是独立替代方案，不能依赖别的候选写入；"
                        "全部只在隔离容器运行，不是连续步骤。不要假设宿主资源、可用网络或未声明依赖。"
                        "不要 Markdown，不执行命令，不声称成功。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"instruction": instruction}, ensure_ascii=False),
                },
            ],
            StrategyProposal,
        )
        return proposal.commands
