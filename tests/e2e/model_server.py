"""仅供网页 CI 的分类与候选选择夹具；不提供代码、实验设计或执行结果。"""

import json
from typing import Any

from fastapi import FastAPI, HTTPException

from openresearch.models.planning import IntentDraft, SourceSelection

app = FastAPI()


@app.post("/v1/chat/completions")
def classify(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages", [])
    schemas = [item.get("content", "") for item in messages if item.get("role") == "system"]
    # 拒绝其它契约，防止把受控分类响应误当成真实模型生成或实验成功。
    if any('"title": "SourceSelection"' in schema for schema in schemas):
        result = SourceSelection(choice=1, reason="Controlled matching source fixture")
    elif any('"title": "IntentDraft"' in schema for schema in schemas):
        context = json.loads(messages[-1]["content"])
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
        raise HTTPException(400, "UI fixture supports classification and source selection only")
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(result.model_dump())},
            }
        ]
    }
