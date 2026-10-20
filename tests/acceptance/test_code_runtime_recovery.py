import json
import os

import pytest
from test_code_recovery_docker import run_code
from test_docker_smoke import pytestmark as pytestmark

from openresearch.llm.client import LLMClient
from openresearch.models.recovery import RecoveryProposal
from openresearch.models.runtime import Status
from openresearch.models.runtime_profiles import RuntimeProfile


def configure(settings):
    settings.runtime_profiles = {
        "cpu13": RuntimeProfile(image="python:3.13-slim-bookworm", python="3.13", memory_mb=512)
    }


async def test_explicit_target_uses_actual_python_313_grammar_and_preserves_source(
    settings, repository, files
):
    configure(settings)
    settings.code_max_repairs = 0
    code = (
        "import sys\n"
        "def identity[T = int](value: T) -> T:\n    return value\n"
        "assert identity.__type_params__[0].__default__ is int\n"
        "assert identity(61) == 61\nassert sys.version_info[:2] == (3, 13)\n"
        "print('ACTUAL_TARGET_GRAMMAR', identity(61), sys.version.split()[0])\n"
    )
    done, report = await run_code(settings, repository, files, code, "用Python3.13执行原代码")
    assert done.status == Status.COMPLETED, [task.error for task in done.nodes]
    assert report["runtime"]["profile"] == "cpu13"
    assert report["attempts"][0]["environment"]["python"].startswith("3.13.")
    assert report["original_code_sha256"] == report["final_code_sha256"]
    assert "ACTUAL_TARGET_GRAMMAR 61 3.13." in report["attempts"][0]["output"]["stdout"]
    preparation = json.loads(files.read(done.artifacts["code_preparation_report"]))
    assert preparation["syntax_validation"] in {"host_ast", "deferred_to_sandbox"}


@pytest.mark.external
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="PyPI opt-in required")
@pytest.mark.parametrize("mode", ["allowed", "budget", "forbidden", "fixed_python"])
async def test_missing_public_package_may_change_runtime_only_with_remaining_budget_and_permission(
    settings, repository, files, monkeypatch, mode
):
    configure(settings)
    settings.code_max_repairs = 1 if mode == "budget" else 2
    code = (
        "import anyenv, sys, importlib.metadata\n"
        "assert importlib.metadata.version('anyenv') == '1.9.7'\n"
        "value = anyenv.load_json('{\"number\": 67}')\nassert value['number'] == 67\n"
        "assert sys.version_info[:2] == (3, 13)\n"
        "print('ACTUAL_CODE_RUNTIME', value['number'], sys.version.split()[0], anyenv.__file__)\n"
    )

    async def propose(self, messages, contract):
        assert contract is RecoveryProposal
        assert "ModuleNotFoundError" in messages[-1]["content"]
        return RecoveryProposal(
            action="dependencies", reason="actual missing module", requirements=["anyenv==1.9.7"]
        )

    monkeypatch.setattr(LLMClient, "request_typed", propose)
    intent = "保持anyenv==1.9.7运行原代码与断言"
    if mode == "forbidden":
        intent += "，不允许切换Python版本"
    elif mode == "fixed_python":
        intent += "，使用Python==3.12"
    done, report = await run_code(settings, repository, files, code, intent)
    assert report["original_code_sha256"] == report["final_code_sha256"]
    assert "ModuleNotFoundError" in report["attempts"][0]["output"]["stderr"]
    prepared = report["decisions"][0]["preparation"]
    assert prepared["attempts"][0]["exit_code"] != 0
    if mode == "allowed":
        assert done.status == Status.COMPLETED, [task.error for task in done.nodes]
        assert report["runtime"]["profile"] == "cpu13"
        assert [item["output"]["exit_code"] for item in report["attempts"]] == [1, 0]
        assert len({item["sandbox_id"] for item in report["attempts"]}) == 2
        assert prepared["decisions"][0]["runtime_after"]["profile"] == "cpu13"
        assert report["attempts"][-1]["environment"]["installed"]["anyenv"] == "1.9.7"
        assert "ACTUAL_CODE_RUNTIME 67 3.13." in report["attempts"][-1]["output"]["stdout"]
    else:
        assert done.status == Status.FAILED and len(report["attempts"]) == 1
        assert not any(item["accepted"] for item in prepared["decisions"])
        assert not report["decisions"][0]["accepted"]
