"""来源/依赖/库调用与容器真实执行；仅来源适用性模型使用明确夹具。"""

import asyncio
import json

from test_framework_libraries import pytestmark as pytestmark
from test_legacy_api import completed_result
from test_legacy_api import legacy_docker as legacy_docker

from openresearch.llm.client import LLMClient
from openresearch.models.comparison import FrameworkAssessment
from openresearch.models.runtime import Status


def request(kind, inputs):
    return {
        "task_type": kind,
        "task_description": "比较 LangChain 与 LlamaIndex，使用冻结来源和共同数据，不要自动修复",
        "assigned_to": (
            "librarian_agent"
            if kind == "framework_research"
            else "coder_agent"
            if "prepare" in kind
            else "sandbox_agent"
            if "run_" in kind
            else "data_agent"
        ),
        "inputs": inputs,
    }


async def test_old_framework_stages_execute_real_libraries_once_and_share_frozen_sources(
    legacy_docker,
    repository,
    files,
    settings,
    monkeypatch,
):
    client, sandbox = legacy_docker
    settings.task_timeout_seconds = 600
    settings.plan_timeout_seconds = 1800
    created = []
    create = sandbox.create

    async def record(plan_id, execution_id):
        identifier = await create(plan_id, execution_id)
        created.append(identifier)
        return identifier

    async def assess(self, messages, model, **kwargs):
        assert model is FrameworkAssessment
        spans = json.loads(messages[-1]["content"])["sources"]
        assert spans and spans[0]["quote"]
        return FrameworkAssessment(
            response="受控提议，仅引用本次实际公开包元数据。", source_ids=[0]
        )

    monkeypatch.setattr(sandbox, "create", record)
    monkeypatch.setattr(LLMClient, "request_typed", assess)
    spec = {
        "mode": "benchmark",
        "answer_mode": "none",
        "targets": [{"name": "LangChain"}, {"name": "LlamaIndex"}],
    }
    async with asyncio.timeout(1800):
        source = completed_result(
            await client.post(
                "/api/execute",
                json=request(
                    "framework_research",
                    {"comparison": spec},
                ),
            )
        )
        root = repository.get_plan(source["plan_id"])
        assert root.status == Status.PENDING and not created
        catalog = json.loads(source["outputs"]["framework_catalog"])
        frozen = root.artifacts["framework_catalog"]
        started = root.usage.started_at
        reference = {"workflow_ref": source["workflow_ref"]}
        for branch in ("left", "right"):
            prepared = completed_result(
                await client.post(
                    "/api/execute",
                    json=request(
                        "framework_prepare_" + branch,
                        reference,
                    ),
                )
            )
            assert prepared["plan_id"] == root.id and "generated_code" in prepared["outputs"]
            configuration = json.loads(prepared["outputs"][branch + "_env"])
            assert not configuration["installation_verified"]
            assert configuration["state"] == "code_and_lock_prepared"
            partial = repository.get_plan(root.id)
            assert all(
                node.run_count == 0
                for node in partial.nodes
                if node.type in {"benchmark_preflight", "research_execute"}
            )
        assert len(created) == 2  # 两次实际解释器探测；没有运行预测程序。
        for index in (1, 2):
            executed = completed_result(
                await client.post(
                    "/api/execute",
                    json=request(
                        f"framework_run_{index}",
                        reference,
                    ),
                )
            )
            metrics = json.loads(executed["outputs"][f"framework_metrics_{index}"])
            assert metrics["framework_module_usage"] == metrics["prediction_coverage"] == 1.0
            prefix = f"framework_{index}__"
            state = repository.get_plan(root.id)
            report = json.loads(files.read(state.artifacts[prefix + "research_report"]))
            actual = report["runs"][0]
            assert report["success"] and actual["execution_seconds"] > 0
            package = catalog["studies"][index - 1]["report"]["packages"][0]
            assert actual["environment"]["installed"][package["name"]] == package["version"]
            method = "similarity_search" if index == 1 else "retrieve"
            assert any(
                call.endswith("." + method)
                for calls in actual["module_calls"].values()
                for call in calls
            )
        final = completed_result(
            await client.post("/api/execute", json=request("framework_report", reference))
        )
        done = repository.get_plan(root.id)
        assert done.status == Status.COMPLETED and done.usage.started_at == started
        assert done.artifacts["framework_catalog"] == frozen and len(created) == 6
        assert len(done.nodes) == 15 and all(node.run_count == 1 for node in done.nodes)
        comparison = json.loads(final["outputs"]["comparison_metrics"])
        assert comparison["complete"] and comparison["protocol"]["origin"] == "synthetic"
        assert all(
            row["dataset_sha256"] == comparison["protocol"]["dataset_sha256"]
            for row in comparison["frameworks"]
        )
        assert final["outputs"]["evaluation_report"] == final["outputs"]["comparison_report"]
        replay = completed_result(
            await client.post("/api/execute", json=request("framework_run_left", reference))
        )
        assert replay["plan_id"] == root.id and len(created) == 6
    assert not await sandbox.inventory()
