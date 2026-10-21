import json

import pytest

from openresearch.agents.framework_report import framework_report
from openresearch.models.comparison import ComparisonSpec, FrameworkTarget
from openresearch.models.context import Conversation, Identity
from openresearch.models.runtime import Status
from openresearch.models.submissions import PlanSubmission
from openresearch.research.benchmark import parse_dataset
from openresearch.research.framework_protocol import create_protocol
from openresearch.services.intent import IntentParser
from openresearch.services.plans import PlanService
from openresearch.services.uploads import UploadService


def comparison_plan(settings, repository, mode="benchmark"):
    return PlanService(settings, repository).build(
        PlanSubmission(
            "对比 LangChain 与 LlamaIndex",
            Identity("alice", "comparison"),
            workflow="framework",
            comparison=ComparisonSpec(
                mode=mode,
                targets=[FrameworkTarget(name="LangChain"), FrameworkTarget(name="LlamaIndex")],
            ),
        )
    )


def test_synthetic_protocol_is_explicit_shared_and_withholds_targets(settings, repository):
    plan = comparison_plan(settings, repository)
    uploads = UploadService(settings, repository)
    result = create_protocol(settings, uploads, plan, plan.nodes[0])
    from openresearch.models.comparison import ComparisonProtocol

    protocol = ComparisonProtocol.model_validate_json(result.outputs["comparison_protocol"])
    assert protocol.origin == "synthetic" and protocol.sample_count == 4
    assert protocol.answer_column == "answer" and "合成" in protocol.scope
    record, raw = uploads.content(protocol.benchmark.upload_id, "alice")
    dataset = parse_dataset(raw, protocol.benchmark, 8)
    assert dataset.sha256 == record.sha256 == protocol.dataset_sha256
    assert all(set(row["inputs"]) == {"query", "documents"} for row in dataset.inputs)
    assert all(
        "target" not in row["inputs"] and "answer" not in row["inputs"] for row in dataset.inputs
    )


def test_research_protocol_does_not_create_a_dataset_or_fake_run(settings, repository):
    plan = comparison_plan(settings, repository, "research")
    result = create_protocol(settings, UploadService(settings, repository), plan, plan.nodes[0])
    protocol = json.loads(result.outputs["comparison_protocol"])
    assert protocol["origin"] == "none" and protocol["benchmark"] is None
    assert not any(task.type == "research_execute" for task in plan.nodes)


def test_failed_branches_still_produce_an_honest_chat_report(settings, repository, files):
    plan = comparison_plan(settings, repository)
    for task in plan.nodes[:-1]:
        task.status = Status.FAILED
        task.error = "actual dependency failure"
    result = framework_report(plan, plan.nodes[-1], files)
    metrics = json.loads(result.outputs["comparison_metrics"])
    assert metrics["complete"] is False
    assert all(not row["complete"] for row in metrics["frameworks"])
    assert "actual dependency failure" in result.outputs["comparison_report"]
    assert "未完整通过" in json.loads(result.outputs["chat_answer"])["response"]


@pytest.mark.parametrize(
    ("message", "mode"),
    [
        ("对比 LangChain 和 LlamaIndex 的 RAG 性能", "benchmark"),
        ("比较 LangChain 和 LlamaIndex 的适用场景", "research"),
    ],
)
async def test_named_comparison_does_not_require_repository_or_manual_configuration(
    settings, message, mode
):
    snapshot = Conversation(owner_id="alice", session_id="compare")
    draft = await IntentParser(settings).parse(snapshot, message, [])
    assert draft.workflow == "framework" and draft.framework_mode == mode
    assert draft.frameworks == ["LangChain", "LlamaIndex"]
