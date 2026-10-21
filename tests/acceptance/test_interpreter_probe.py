import json
import os

import httpx
import pytest
from test_docker_smoke import pytestmark as pytestmark
from test_runtime_profiles import runtime_lease

from openresearch.errors import DependencyInstallError, EnvironmentLockError
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.models.sources import EnvironmentLock
from openresearch.research.code_execution import CodeProgramRunner
from openresearch.research.environment import install_environment
from openresearch.research.environment_preparation import EnvironmentPreparation
from openresearch.research.interpreter import RuntimeInterpreterProbe
from openresearch.research.runtime_selection import bind_runtime
from openresearch.sandbox.docker import DockerSandbox
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.http import HttpSandbox
from openresearch.sandbox.service import create_sandbox_app
from openresearch.sandbox.session import SandboxSessions
from openresearch.sources.dependency_resolution import DependencyResolver
from openresearch.sources.wheels import WheelSource, validate_closure


@pytest.mark.parametrize("python", ["3.11", "3.12", "3.13"])
@pytest.mark.parametrize("http", [False, True])
async def test_actual_interpreter_precedes_resolution_and_survives_direct_or_http_installation(
    settings, repository, python, http
):
    settings.runtime_profiles = {
        "target": RuntimeProfile(image="python:" + python + "-slim-bookworm", python=python)
    }
    sandbox = DockerSandbox(settings)
    if http:
        native = sandbox
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(create_sandbox_app(settings, FallbackSandbox(native))),
            base_url="http://sandbox",
        )
        sandbox = HttpSandbox(settings, client)
    lease = runtime_lease(settings, repository)
    sessions = SandboxSessions(repository, sandbox)
    binding = bind_runtime(settings, "target")
    try:
        actual = await RuntimeInterpreterProbe(settings, sessions, lease, "run")(binding)
        assert actual.python == python and actual.python_full_version != python + ".0"
        assert actual.container_image_id.startswith("sha256:")
        assert not repository.cleanup_candidates(sandbox.backend)
        # 同一条件旧解析器按 .0 错误跳过；实际版本必须要求 helper，不能漏锁后再进入程序。
        graph = {
            "example": {
                "info": {
                    "requires_dist": [
                        "helper; python_full_version >= '" + actual.python_full_version + "'"
                    ]
                }
            }
        }
        validate_closure(graph, {"example": "1"}, python)
        with pytest.raises(EnvironmentLockError, match="helper"):
            validate_closure(
                graph, {"example": "1"}, python, full_version=actual.python_full_version
            )
        spec = await DependencyResolver(python).for_interpreter(actual).resolve([])
        lock, wheels = (
            await WheelSource(settings, python=python).for_interpreter(actual).fetch(spec)
        )
        assert not wheels and lock.interpreter == actual
        async with sessions.open(lease, "target") as session:
            environment = await install_environment(session, lock, settings)
            assert environment.python == actual.python_full_version
            assert environment.container_image_id == actual.container_image_id
            result = await session.execute(
                "import sys\nassert sys.version_info[:2] == "
                + repr((3, int(python.split(".")[1])))
                + "\nprint(43)\n"
            )
            assert result.exit_code == 0 and result.stdout.strip() == "43"
        assert not await sandbox.inventory()
    finally:
        await sandbox.shutdown()


async def test_actual_patch_drift_stops_before_offline_installation(settings, repository):
    sandbox = DockerSandbox(settings)
    lease = runtime_lease(settings, repository)
    sessions = SandboxSessions(repository, sandbox)
    try:
        actual = await RuntimeInterpreterProbe(settings, sessions, lease, "run")(None)
        frozen = actual.model_copy(update={"python_full_version": "3.12.0"})
        lock = EnvironmentLock(interpreter=frozen)
        async with sessions.open(lease) as session:
            with pytest.raises(DependencyInstallError) as captured:
                await install_environment(session, lock, settings)
            assert captured.value.result.exit_code != 0
            assert (
                "actual interpreter changed after dependency resolution"
                in captured.value.result.stderr
            )
        assert not await sandbox.inventory()
    finally:
        await sandbox.shutdown()


async def test_empty_dependency_code_still_rechecks_frozen_interpreter_before_execution(
    settings, repository
):
    sandbox = DockerSandbox(settings)
    lease = runtime_lease(settings, repository)
    sessions = SandboxSessions(repository, sandbox)
    try:
        actual = await RuntimeInterpreterProbe(settings, sessions, lease, "run")(None)
        lock = EnvironmentLock(
            interpreter=actual.model_copy(update={"python_full_version": "3.12.0"})
        )
        outcome = await CodeProgramRunner(settings, sessions).run(
            lease, "raise AssertionError('SHOULD_NOT_EXECUTE')", lock, 1
        )
        assert outcome.attempt.phase == "install" and not outcome.attempt.execution_requested
        assert outcome.output.exit_code != 0
        assert "actual interpreter changed after dependency resolution" in outcome.attempt.error
        assert "SHOULD_NOT_EXECUTE" not in outcome.output.stderr
        assert not await sandbox.inventory()
    finally:
        await sandbox.shutdown()


@pytest.mark.external
@pytest.mark.skipif(
    os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="real PyPI opt-in required"
)
async def test_real_patch_constrained_package_is_resolved_and_installed_without_version_repair(
    settings, repository
):
    settings.runtime_profiles = {
        "target": RuntimeProfile(image="python:3.12-slim-bookworm", python="3.12")
    }
    sandbox = DockerSandbox(settings)
    lease = runtime_lease(settings, repository)
    sessions = SandboxSessions(repository, sandbox)
    try:
        probe = RuntimeInterpreterProbe(settings, sessions, lease, "保留 githide==0.1.0")
        binding = bind_runtime(settings, "target")
        actual = await probe(binding)
        prepared = await EnvironmentPreparation(settings).run(
            "保留 githide==0.1.0",
            ["githide==0.1.0"],
            ["githide==0.1.0"],
            0,
            lock_wheels=True,
            runtime=binding,
            allow_runtime_select=True,
            probe=probe,
        )
        assert prepared.report.success, prepared.report.model_dump_json()
        assert len(prepared.report.attempts) == 1 and not prepared.report.decisions
        assert prepared.report.attempts[0].interpreter == actual
        lock = prepared.lock
        assert lock is not None
        assert lock.interpreter == actual
        assert any(wheel.requirement == "githide==0.1.0" for wheel in lock.wheels)
        async with sessions.open(lease, "target") as session:
            environment = await install_environment(session, lock, settings)
            result = await session.execute(
                "import sys, json\nsys.path.insert(0, '/workspace/environment')\n"
                "import githide\nfrom importlib.metadata import version, distribution\n"
                "from contextlib import redirect_stdout\nimport io\n"
                "assert version('githide') == '0.1.0'\n"
                "entry = next(e for e in distribution('githide').entry_points "
                "if e.group == 'console_scripts')\n"
                "sys.argv = [entry.name, '--help']\n"
                "output = io.StringIO()\n"
                "with redirect_stdout(output):\n"
                "    try:\n"
                "        entry.load()()\n"
                "    except SystemExit as exited:\n"
                "        assert exited.code in (0, None), exited.code\n"
                "assert 'usage:' in output.getvalue().lower(), output.getvalue()\n"
                "assert 'GitHide CLI Tool' in output.getvalue()\n"
                "print(json.dumps({'version':version('githide'), 'path':githide.__file__, "
                "'python':sys.version.split()[0]}))\n"
            )
            assert result.exit_code == 0, result.stderr
            observed = json.loads(result.stdout)
            assert (
                observed["version"] == "0.1.0" and observed["python"] == actual.python_full_version
            )
            assert observed["path"].startswith("/workspace/environment/githide/")
            assert environment.python == actual.python_full_version
        assert not await sandbox.inventory()
    finally:
        await sandbox.shutdown()
