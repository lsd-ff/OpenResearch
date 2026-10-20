import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import httpx
import pytest
from test_docker_smoke import pytestmark as pytestmark

from openresearch.errors import DependencyInstallError
from openresearch.models.execution import ExecutionLease, TaskCompletion
from openresearch.models.runtime import Plan, Task, TaskContract, TaskResult
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.models.sources import EnvironmentSpec
from openresearch.research.environment import install_environment
from openresearch.sandbox.docker import DockerSandbox
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.http import HttpSandbox
from openresearch.sandbox.service import create_sandbox_app
from openresearch.sandbox.session import SandboxSessions
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.dependency_resolution import DependencyResolver
from openresearch.sources.wheels import WheelSource


def runtime_lease(settings, repository):
    task = Task(
        name="runtime acceptance",
        type="code_execute",
        assigned_to="coder_agent",
        contract=TaskContract(allowed_tools=["sandbox.python"]),
    )
    plan = Plan(
        user_intent="actual runtime verification",
        owner_id="runtime",
        session_id="runtime",
        nodes=[task],
    )
    repository.save_plan(plan)
    repository.queue_plan(plan.id, plan.owner_id)
    claimed = repository.claim_next("runtime-acceptance", 300, 2)
    assert claimed is not None
    return ExecutionLease.for_task(*claimed, "runtime-acceptance")


def fixture_wheel():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        prefix = "runtime_probe-1.0.dist-info/"
        archive.writestr(
            prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        archive.writestr(
            prefix + "METADATA",
            "Metadata-Version: 2.1\nName: runtime-probe\nVersion: 1.0\nRequires-Python: >=3.11\n",
        )
        archive.writestr(prefix + "RECORD", "")
        archive.writestr("runtime_probe/__init__.py", "value = 37\n")
    return stream.getvalue()


async def probe_lock(settings, python):
    raw = fixture_wheel()

    def provider(request):
        if request.url.host == "pypi.org":
            return httpx.Response(
                200,
                json={
                    "info": {
                        "name": "runtime-probe",
                        "version": "1.0",
                        "requires_python": ">=3.11",
                    },
                    "urls": [
                        {
                            "filename": "runtime_probe-1.0-py3-none-any.whl",
                            "packagetype": "bdist_wheel",
                            "url": "https://files.pythonhosted.org/runtime-probe.whl",
                            "size": len(raw),
                            "digests": {"sha256": hashlib.sha256(raw).hexdigest()},
                        }
                    ],
                },
            )
        return httpx.Response(200, content=raw)

    lock, wheels = await WheelSource(settings, httpx.MockTransport(provider), python=python).fetch(
        EnvironmentSpec(requirements=["runtime-probe==1.0"])
    )
    blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)
    for wheel in wheels.values():
        blobs.write(wheel)
    return lock


@pytest.mark.parametrize("python", ["3.11", "3.12", "3.13"])
@pytest.mark.parametrize("http", [False, True])
async def test_named_runtime_installs_frozen_wheel_under_the_actual_target_interpreter(
    settings, repository, python, http
):
    reference = "python:" + python + "-slim-bookworm"
    settings.runtime_profiles = {
        "selected": RuntimeProfile(image=reference, python=python, memory_mb=256)
    }
    native = DockerSandbox(settings)
    sandbox = native
    if http:
        app = create_sandbox_app(settings, FallbackSandbox(native))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://sandbox")
        sandbox = HttpSandbox(settings, client)
    lock = await probe_lock(settings, python)
    lease = runtime_lease(settings, repository)
    try:
        async with SandboxSessions(repository, sandbox).open(lease, "selected") as session:
            environment = await install_environment(session, lock, settings)
            assert environment.python.startswith(python + ".")
            assert environment.container_image == reference
            assert environment.container_image_id.startswith("sha256:")
            result = await session.execute(
                "import sys, json\nsys.path.insert(0, '/workspace/environment')\n"
                "import runtime_probe\n"
                "assert runtime_probe.value == 37\n"
                "print(json.dumps({'version': sys.version_info[:2], 'value': runtime_probe.value, "
                "'path': runtime_probe.__file__}))\n"
            )
            assert result.exit_code == 0, result.stderr
            actual = json.loads(result.stdout)
            assert actual["version"] == [3, int(python.split(".")[1])] and actual["value"] == 37
            assert actual["path"] == "/workspace/environment/runtime_probe/__init__.py"
        assert not repository.cleanup_candidates(sandbox.backend)
        repository.finish(lease, TaskCompletion(result=TaskResult()))
    finally:
        await sandbox.shutdown()


async def test_mislabeled_runtime_image_cannot_report_a_different_python_version(
    settings, repository
):
    settings.runtime_profiles = {
        "mislabeled": RuntimeProfile(image="python:3.12-slim-bookworm", python="3.13")
    }
    sandbox = DockerSandbox(settings)
    lease = runtime_lease(settings, repository)
    try:
        async with SandboxSessions(repository, sandbox).open(lease, "mislabeled") as session:
            with pytest.raises(DependencyInstallError) as error:
                await install_environment(session, await probe_lock(settings, "3.13"), settings)
            assert error.value.result.exit_code != 0
            assert "environment lock requires CPython" in error.value.result.stderr
        assert not repository.cleanup_candidates("docker")
    finally:
        await sandbox.shutdown()


@pytest.mark.external
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_EXTERNAL") != "1", reason="PyPI opt-in required")
@pytest.mark.parametrize("python", ["3.11", "3.12", "3.13"])
async def test_real_pypi_binary_wheel_resolves_and_loads_in_the_matching_runtime(
    settings, repository, python
):
    settings.runtime_profiles = {
        "selected": RuntimeProfile(image="python:" + python + "-slim-bookworm", python=python)
    }
    spec = await DependencyResolver(python).resolve(["PyYAML==6.0.3"])
    lock, wheels = await WheelSource(settings, python=python).fetch(spec)
    blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)
    for raw in wheels.values():
        blobs.write(raw)
    sandbox = DockerSandbox(settings)
    lease = runtime_lease(settings, repository)
    try:
        async with SandboxSessions(repository, sandbox).open(lease, "selected") as session:
            environment = await install_environment(session, lock, settings)
            assert environment.python.startswith(python + ".")
            result = await session.execute(
                "import sys, json\nsys.path.insert(0, '/workspace/environment')\n"
                "import yaml, yaml._yaml\nassert yaml.__with_libyaml__\n"
                "value = yaml.safe_load('number: 41')\nassert value['number'] == 41\n"
                "print(json.dumps({'value': value['number'], 'version': yaml.__version__, "
                "'extension': yaml._yaml.__file__}))\n"
            )
            assert result.exit_code == 0, result.stderr
            actual = json.loads(result.stdout)
            assert actual["value"] == 41 and actual["version"] == "6.0.3"
            assert "cpython-" + python.replace(".", "") in actual["extension"]
        assert not repository.cleanup_candidates("docker")
    finally:
        await sandbox.shutdown()


@pytest.mark.gpu
@pytest.mark.skipif(os.getenv("OPENRESEARCH_TEST_GPU") != "1", reason="real GPU opt-in required")
async def test_named_gpu_profile_executes_an_actual_cuda_kernel_through_http(settings, repository):
    settings.runtime_profiles = {
        "cuda12": RuntimeProfile(
            image="python:3.12-slim-bookworm", python="3.12", gpu=True, memory_mb=512
        )
    }
    native = DockerSandbox(settings)
    app = create_sandbox_app(settings, FallbackSandbox(native))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://sandbox")
    sandbox = HttpSandbox(settings, client)
    lease = runtime_lease(settings, repository)
    code = (Path(__file__).parents[1] / "fixtures/cuda_vector_add.py").read_bytes()
    try:
        async with SandboxSessions(repository, sandbox).open(lease, "cuda12") as session:
            await session.put_files({"cuda_probe.py": code})
            result = await session.execute(
                "import os, runpy\nos.environ['CUDA_CACHE_DISABLE'] = '1'\n"
                "runpy.run_path('/tmp/openresearch/cuda_probe.py', run_name='__main__')\n"
            )
            assert result.exit_code == 0, result.stderr
            actual = json.loads(result.stdout)
            assert actual["values"] == [11.0, 22.0, 33.0, 44.0]
            assert actual["device"] and actual["python"].startswith("3.12.")
        assert not repository.cleanup_candidates("http")
    finally:
        await sandbox.shutdown()
