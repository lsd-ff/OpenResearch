"""仅供网页 CI 的意图分类夹具；不提供代码、实验设计或执行结果。"""

import json
from typing import Any

from fastapi import FastAPI, HTTPException

from openresearch.models.planning import IntentDraft

app = FastAPI()


@app.post("/v1/chat/completions")
def classify(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages", [])
    schemas = [item.get("content", "") for item in messages if item.get("role") == "system"]
    # 拒绝其它契约，防止把受控分类响应误当成真实模型生成或实验成功。
    if not any('"title": "IntentDraft"' in schema for schema in schemas):
        raise HTTPException(400, "UI fixture supports IntentDraft only")
    draft = IntentDraft(
        workflow="benchmark",
        repository_url="https://github.com/pallets/itsdangerous",
        input_columns=["x"],
        target_column="outcome",
        id_column="id",
    )
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(draft.model_dump())},
            }
        ]
    }
