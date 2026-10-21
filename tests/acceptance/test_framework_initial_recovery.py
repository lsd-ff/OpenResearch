import json
import os
from unittest.mock import AsyncMock

import pytest
from test_docker_smoke import pytestmark as pytestmark

from openresearch.models.comparison import FrameworkTarget
from openresearch.models.environment_repair import EnvironmentRepairProposal
from openresearch.research.environment import environment_files, installation_program
from openresearch.research.environment_preparation import EnvironmentPreparation
from openresearch.sandbox.docker import DockerSandbox
from openresearch.sources.frameworks import FrameworkSources


@pytest.mark.external
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="PyPI opt-in required")
async def test_framework_initial_resolution_recovery_binds_actual_adopted_api_version(settings):
    # 包识别与修复提议为夹具；解析失败、PyPI 元数据、wheel 与容器输出均为实际结果。
    sources = FrameworkSources(settings)
    sources.llm.request_typed = AsyncMock(
        return_value=FrameworkTarget(
            name="PyYAML",
            requirements=["PyYAML==99999"],
            import_roots=["yaml"],
        )
    )
    sources.preparation.repairer.diagnose = AsyncMock(
        return_value=EnvironmentRepairProposal(
            action="dependencies",
            reason="replace the model-proposed nonexistent version",
            requirements=["PyYAML==6.0.3"],
        )
    )
    prepared = await sources.prepare(FrameworkTarget(name="PyYAML"), "使用 PyYAML 实际解析 YAML", 1)
    assert prepared.report.success and len(prepared.report.attempts) == 2
    assert prepared.report.attempts[0].exit_code != 0
    assert prepared.source.packages[0].version == "6.0.3"
    assert prepared.source.packages[0].metadata_url.endswith("/6.0.3/json")
    assert any(path.startswith("yaml/") for path in prepared.source.api_excerpts)
    assert prepared.lock.wheels[0].requirement == "pyyaml==6.0.3"
    await actual_yaml(settings, prepared.lock)


async def actual_yaml(settings, lock):
    sandbox = DockerSandbox(settings)
    identifier = await sandbox.create("framework-recovery", "adopted-version")

    async def log(stream, message):
        pass

    try:
        await sandbox.put_files(identifier, await environment_files(lock, settings))
        installation = await sandbox.execute(identifier, installation_program(), log)
        assert installation.exit_code == 0, installation.stderr
        result = await sandbox.execute(
            identifier,
            "import sys, json\nsys.path.insert(0, '/workspace/environment')\nimport yaml\n"
            "value = yaml.safe_load('number: 23')\nassert value['number'] == 23\n"
            "print(json.dumps({'version': yaml.__version__, 'path': yaml.__file__, "
            "'value': value['number']}))\n",
            log,
        )
        assert result.exit_code == 0, result.stderr
        actual = json.loads(result.stdout)
        assert actual == {
            "version": "6.0.3",
            "path": "/workspace/environment/yaml/__init__.py",
            "value": 23,
        }
    finally:
        await sandbox.close(identifier)


@pytest.mark.external
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="PyPI opt-in required")
async def test_wrong_model_package_is_explicitly_replaced_then_original_yaml_program_runs(settings):
    preparation = EnvironmentPreparation(settings)
    preparation.repairer.diagnose = AsyncMock(
        return_value=EnvironmentRepairProposal(
            action="dependencies",
            reason="replace the erroneous distribution with the requested real library",
            requirements=["PyYAML==6.0.3"],
            remove_requirements=["yaml"],
        )
    )
    prepared = await preparation.run(
        "使用 PyYAML==6.0.3 运行原程序，保持断言", ["yaml==99999"], [], 1, lock_wheels=True
    )
    assert prepared.report.success and len(prepared.report.attempts) == 2
    assert prepared.report.attempts[0].exit_code != 0
    assert prepared.report.decisions[0].proposal.remove_requirements == ["yaml"]
    assert prepared.spec.requirements == ["pyyaml==6.0.3"]
    await actual_yaml(settings, prepared.lock)


async def test_removed_stdlib_dependency_keeps_original_python_library_and_executes_real_copy(
    settings,
):
    preparation = EnvironmentPreparation(settings)
    preparation.repairer.diagnose = AsyncMock(
        return_value=EnvironmentRepairProposal(
            action="dependencies",
            reason="the actual Python standard library requires no wheel",
            remove_requirements=["shutil"],
        )
    )
    prepared = await preparation.run(
        "使用 shutil 复制实际文件，保留原程序", ["shutil==0"], [], 1, lock_wheels=True
    )
    assert prepared.report.success and prepared.lock.wheels == []
    assert prepared.report.attempts[0].exit_code is None
    assert "standard library" in prepared.report.attempts[0].diagnostic
    assert prepared.report.decisions[0].proposal.remove_requirements == ["shutil"]
    sandbox = DockerSandbox(settings)
    identifier = await sandbox.create("stdlib-recovery", "actual-copy")

    async def log(stream, message):
        pass

    code = (
        "import pathlib, shutil, json\nroot = pathlib.Path('/tmp/openresearch')\n"
        "root.mkdir(parents=True, exist_ok=True)\n"
        "source, target = root / 'source.txt', root / 'target.txt'\n"
        "source.write_text('ACTUAL_COPY_29')\nshutil.copyfile(source, target)\n"
        "assert target.read_text() == 'ACTUAL_COPY_29'\n"
        "print(json.dumps({'copied': target.read_text(), 'module': shutil.__file__}))\n"
    )
    try:
        result = await sandbox.execute(identifier, code, log)
        assert result.exit_code == 0, result.stderr
        actual = json.loads(result.stdout)
        assert actual["copied"] == "ACTUAL_COPY_29"
        assert actual["module"] == "/usr/local/lib/python3.12/shutil.py"
    finally:
        await sandbox.close(identifier)
