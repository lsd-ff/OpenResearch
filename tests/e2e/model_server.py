"""网页 CI 的分类、候选、预定修复及指定通用回答；不提供实验结果。"""

import json
from typing import Any

from fastapi import FastAPI, HTTPException

from openresearch.models.gap import GapProposal
from openresearch.models.knowledge import KnowledgeProposal
from openresearch.models.planning import IntentDraft, SourceSelection
from openresearch.models.recovery import RecoveryProposal
from openresearch.models.research import FilePatch, ResearchPatch

app = FastAPI()


@app.post("/v1/chat/completions")
def classify(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages", [])
    schemas = [item.get("content", "") for item in messages if item.get("role") == "system"]
    # 拒绝其它契约，防止把受控分类响应误当成真实模型生成或实验成功。
    if any('"title": "RecoveryProposal"' in schema for schema in schemas):
        context = json.loads(messages[-1]["content"])
        if "json.decode" not in context["code"] or "AttributeError" not in context["error"]:
            raise HTTPException(400, "only the controlled JSON API repair fixture is supported")
        result = RecoveryProposal(
            action="code",
            reason="Controlled JSON API correction.",
            code=context["code"].replace("json.decode", "json.loads"),
        )
    elif any('"title": "GapProposal"' in schema for schema in schemas):
        context = json.loads(messages[-1]["content"])
        if context["request"] not in {"UI_GAP_NO_PATCH_FIXTURE", "UI_GAP_REJECTED_PATCH_FIXTURE"}:
            raise HTTPException(400, "only the explicit no-patch display fixture is supported")
        result = GapProposal(
            status="insufficient_evidence", diagnosis="网页夹具：没有足够证据支持补丁。"
        )
        if context["request"] == "UI_GAP_REJECTED_PATCH_FIXTURE":
            entry = context["allowed_paths"]["openresearch_run.py"]
            result = GapProposal(
                status="patched",
                diagnosis="网页负向夹具：此补丁必须被拒绝。",
                patch=ResearchPatch(
                    files=[
                        FilePatch(
                            path="openresearch_run.py",
                            base_sha256=entry["base_sha256"],
                            content=entry["content"].replace("assert sum([2,3]) == 5\n", ""),
                        )
                    ]
                ),
            )
    elif any('"title": "KnowledgeProposal"' in schema for schema in schemas):
        context = json.loads(messages[-1]["content"])
        if context["stage"] != "knowledge_process" or context["sources"] or context["pages"]:
            raise HTTPException(
                400, "only the unsourced general response display fixture is supported"
            )
        result = KnowledgeProposal(response="这是通用回答展示夹具；没有执行代码、测试或实验。")
    elif any('"title": "SourceSelection"' in schema for schema in schemas):
        result = SourceSelection(choice=1, reason="Controlled matching source fixture")
    elif any('"title": "IntentDraft"' in schema for schema in schemas):
        context = json.loads(messages[-1]["content"])
        if "分析数据" in context["message"]:
            result = IntentDraft(workflow="data")
            return {"choices": [{"message": {"content": result.model_dump_json()}}]}
        result = (
            IntentDraft(
                workflow="reproduce",
                paper_query="Transformer: Attention Is All You Need",
                repository_url="https://github.com/",
            )
            if "Transformer" in context["message"]
            else IntentDraft(
                workflow="benchmark",
                repository_url="https://github.com/pallets/itsdangerous",
                input_columns=["x"],
                target_column="outcome",
                id_column="id",
            )
        )
    else:
        raise HTTPException(
            400, "UI fixture supports classification, source selection and JSON API repair only"
        )
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(result.model_dump())},
            }
        ]
    }
