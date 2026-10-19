import pytest
from pydantic import ValidationError

from openresearch.config import Settings
from openresearch.errors import ContractError
from openresearch.models.runtime import Plan
from openresearch.planner.code import build_code_plan


def test_rejects_cycles(settings: Settings) -> None:
    plan = build_code_plan("smoke", "alice", "session", settings)
    plan.nodes[0].dependencies = [plan.nodes[1].id]
    with pytest.raises(ValidationError, match="cycle"):
        Plan.model_validate(plan.model_dump(by_alias=True))


def test_rejects_artifacts_without_upstream_producer(settings: Settings) -> None:
    plan = build_code_plan("smoke", "alice", "session", settings)
    plan.nodes[1].dependencies = []
    with pytest.raises(ValidationError, match="ancestor"):
        Plan.model_validate(plan.model_dump(by_alias=True))


def test_research_without_assets_does_not_become_mock_experiment(settings: Settings) -> None:
    with pytest.raises(ContractError, match="需要真实材料"):
        build_code_plan("复现论文的 smoke 实验", "alice", "session", settings)


def test_fenced_code_is_preserved(settings: Settings) -> None:
    plan = build_code_plan("运行代码\n```python\nprint(42)\n```", "alice", "session", settings)
    assert plan.nodes[0].inputs["code"] == "print(42)\n"
