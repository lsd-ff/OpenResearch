"""供应商协议只负责线上格式；业务层仍验证自己的 JSON 契约。"""

import json
from dataclasses import dataclass
from typing import Any, Literal

from openresearch.config import Settings
from openresearch.errors import ModelError

JSON_INSTRUCTION = "Return exactly one valid JSON object. No Markdown, code fences or extra text."
RESPONSE_TOOL = "submit_response"


@dataclass(frozen=True)
class ProviderRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any]


def build_request(
    settings: Settings, messages: list[dict[str, str]], schema: dict[str, Any] | None = None
) -> ProviderRequest:
    token = settings.llm_api_key.get_secret_value()
    base = settings.llm_base_url.rstrip("/")
    generation = {
        "model": settings.llm_model,
        "max_tokens": settings.llm_max_output_tokens,
    }
    if settings.llm_thinking == "disabled":
        generation["thinking"] = {"type": "disabled"}
    if settings.llm_provider == "openai":
        return ProviderRequest(
            base + "/chat/completions",
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            {
                **generation,
                "messages": messages,
                "response_format": {"type": "json_object"},
            },
        )
    # Anthropic 的 system 是顶层字段；不能把 OpenAI 的 system 角色直接传过去。
    system = [item["content"] for item in messages if item["role"] == "system"]
    turns = [item for item in messages if item["role"] != "system"]
    if not turns or any(item["role"] not in {"user", "assistant"} for item in turns):
        raise ModelError("model request requires supported conversation roles")
    if settings.llm_structured_output == "tool" and schema is not None:
        generation["tools"] = [
            {
                "name": RESPONSE_TOOL,
                "description": "Submit the structured response that satisfies the provided schema.",
                "input_schema": schema,
            }
        ]
        generation["tool_choice"] = {"type": "tool", "name": RESPONSE_TOOL}
        system.append("Use submit_response to return your answer; do not output explanatory text.")
    return ProviderRequest(
        base + ("/messages" if base.endswith("/v1") else "/v1/messages"),
        {
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        {
            **generation,
            "system": "\n\n".join([*system, JSON_INSTRUCTION]),
            "messages": turns,
            "stream": False,
        },
    )


def response_text(
    provider: Literal["openai", "anthropic"], data: Any, expected_tool: str | None = None
) -> str:
    if provider == "openai":
        choice = data["choices"][0]
        if choice.get("finish_reason") in {"length", "content_filter", "tool_calls"}:
            raise ModelError("model output was truncated, refused or requires unsupported tools")
        text = choice["message"]["content"]
        if not isinstance(text, str):
            raise TypeError("model content is not text")
        return text
    blocks = data["content"]
    if not isinstance(blocks, list):
        raise TypeError("model content is not a list")
    tools = [item for item in blocks if item["type"] == "tool_use"]
    if data.get("stop_reason") == "tool_use" and expected_tool is not None:
        if len(tools) != 1 or tools[0]["name"] != expected_tool:
            raise ModelError("model returned an unexpected structured response tool")
        if not isinstance(tools[0]["input"], dict):
            raise TypeError("structured response tool input is not an object")
        # 该工具只是响应信封；不分派工具、不执行任何供应商返回的操作。
        return json.dumps(tools[0]["input"], ensure_ascii=False, allow_nan=False)
    if data.get("stop_reason") not in {"end_turn", "stop_sequence"} or tools:
        raise ModelError("model output was truncated, refused or requires unsupported tools")
    text_blocks = [item["text"] for item in blocks if item["type"] == "text"]
    if not text_blocks or any(not isinstance(text, str) for text in text_blocks):
        raise TypeError("model response has no valid text blocks")
    # reasoning/tool 内容不能被误当成业务 JSON，只有正式 text 块可进入契约校验。
    return "".join(text_blocks)


def json_document(text: str) -> str:
    content = text.strip()
    lines = content.splitlines()
    if len(lines) >= 3 and lines[0] in {"```json", "```"} and lines[-1] == "```":
        content = "\n".join(lines[1:-1]).strip()
    if not isinstance(json.loads(content), dict):
        raise ValueError("model response must be a JSON object")
    return content
