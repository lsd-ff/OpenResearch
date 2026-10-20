import asyncio
import json
import os

import pytest
from test_docker_smoke import pytestmark as docker_marks
from test_legacy_api import completed_result, payload
from test_legacy_api import legacy_docker as legacy_docker

from openresearch.agents.code_repair import CodeRepairer
from openresearch.models.recovery import RecoveryProposal
from openresearch.models.runtime import Status

pytestmark = [
    *docker_marks,
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="real PyPI opt-in required"
    ),
]


async def prepared_yaml(client):
    requirements = '["PyYAML==6.0.3"]'
    resolved = completed_result(
        await client.post(
            "/api/execute", json=payload("resolve_dependencies", {"dependency_spec": requirements})
        )
    )
    assert json.loads(resolved["result"]) == ["pyyaml==6.0.3"]
    prepared = completed_result(
        await client.post(
            "/api/execute", json=payload("prepare_runtime", {"dependency_spec": requirements})
        )
    )
    installed = completed_result(
        await client.post(
            "/api/execute",
            json=payload(
                "install_dependencies",
                {
                    "runtime_session": prepared["outputs"]["runtime_session"],
                    "dependency_spec": requirements,
                },
            ),
        )
    )
    state = json.loads(installed["outputs"]["legacy_environment_state"])
    assert state["phase"] == "installed" and state["constraints"] == ["pyyaml==6.0.3"]
    assert state["installation"]["installed"] == {"pyyaml": "6.0.3"}
    return installed


async def test_legacy_dependency_chain_installs_published_pin_and_runs_actual_library(
    legacy_docker,
):
    client, sandbox = legacy_docker
    async with asyncio.timeout(240):
        installed = await prepared_yaml(client)
        code = (
            "import yaml\nassert yaml.__version__ == '6.0.3'\n"
            "value = yaml.safe_load('number: 41')['number']\nassert value == 41\n"
            "print('ACTUAL_LEGACY_YAML', value, yaml.__version__, yaml.__file__)\n"
        )
        executed = completed_result(
            await client.post(
                "/api/execute",
                json=payload(
                    "execute_code",
                    {"code": code, "prepared_runtime": installed["outputs"]["prepared_runtime"]},
                ),
            )
        )
    assert "ACTUAL_LEGACY_YAML 41 6.0.3 /workspace/environment/yaml/" in executed["result"]
    assert executed["code"] == code
    report = json.loads(executed["outputs"]["code_recovery_report"])
    assert report["success"] and len(report["attempts"]) == 1 and not report["decisions"]
    assert report["original_code_sha256"] == report["final_code_sha256"]
    assert not await sandbox.inventory()


async def test_prepared_user_pin_cannot_be_downgraded_by_later_repair_proposal(
    legacy_docker, monkeypatch, repository, files
):
    client, sandbox = legacy_docker
    async with asyncio.timeout(240):
        installed = await prepared_yaml(client)

        async def wrong_version(
            self, goal, code, error, requirements, allow_dependencies, **options
        ):
            assert "AttributeError" in error and allow_dependencies
            return RecoveryProposal(
                action="dependencies",
                reason="controlled conflicting version",
                requirements=["PyYAML==6.0.2"],
            )

        monkeypatch.setattr(CodeRepairer, "propose", wrong_version)
        code = "import yaml\nassert yaml.__version__ == '6.0.3'\nyaml.nonexistent_api()\n"
        request = payload(
            "execute_code",
            {"code": code, "prepared_runtime": installed["outputs"]["prepared_runtime"]},
        )
        request["task_description"] = "执行原代码，遇到依赖问题允许有界恢复，不改变原断言。"
        response = await client.post("/api/execute", json=request)
    assert "event: error" in response.text and "event: result" not in response.text
    done = repository.get_plan(response.headers["x-openresearch-plan-id"], "alice")
    assert done.status == Status.FAILED
    report = json.loads(files.read(done.artifacts["code_recovery_report"]))
    assert not report["success"] and len(report["attempts"]) == 1
    assert not report["decisions"][0]["accepted"]
    assert "no solution found" in report["decisions"][0]["error"].lower()
    assert "pyyaml" in report["decisions"][0]["error"].lower()
    assert report["attempts"][0]["environment"]["installed"]["pyyaml"] == "6.0.3"
    assert report["original_code_sha256"] == report["final_code_sha256"]
    assert not await sandbox.inventory()
