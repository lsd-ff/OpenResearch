import json

import httpx
from pydantic import SecretStr

from openresearch.config import Settings
from openresearch.llm.client import LLMClient
from openresearch.watchdog.advisor import LLMAdvisor
from openresearch.watchdog.models import DiagnosisInput


async def test_advisor_sends_diagnostic_evidence_as_untrusted_data(
    settings: Settings,
):
    settings.llm_model = "test-model"
    settings.llm_api_key = SecretStr("private-token")
    received = []

    def provider(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        messages = body["messages"]
        received.append(messages)
        assert any(
            "不可信来源数据" in item["content"] for item in messages if item["role"] == "system"
        )
        evidence = json.loads(messages[-1]["content"])
        assert evidence["exit_code"] == 127
        assert evidence["command"] == "missing-command"
        assert evidence["output"] == "command not found"
        assert evidence["prior_failure_evidence"] == "actual history"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"hint":"请检查镜像中的命令依赖。"}'}}]},
        )

    advisor = LLMAdvisor(LLMClient(settings, httpx.MockTransport(provider)))
    diagnostic = DiagnosisInput("missing-command", "command not found", 127, "actual history")
    assert "依赖" in await advisor.suggest(diagnostic)
    assert len(received) == 1
