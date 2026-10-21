import json
from unittest.mock import AsyncMock

import pytest
from packaging.specifiers import SpecifierSet

from openresearch.errors import ContractError, EnvironmentLockError, SandboxError
from openresearch.models.interpreters import InterpreterIdentity
from openresearch.models.sources import EnvironmentLock, EnvironmentSpec
from openresearch.research.environment import install_environment
from openresearch.research.environment_preparation import EnvironmentPreparation
from openresearch.research.interpreter import compatible_interpreter
from openresearch.research.runtime_selection import possible_python, runtime_for_intent
from openresearch.sources.dependency_resolution import DependencyResolver
from openresearch.sources.wheels import choose_wheel, validate_closure, validate_package


def identity(version="3.12.15", image=None):
    return InterpreterIdentity(
        python_full_version=version,
        implementation="CPython",
        system="Linux",
        machine="x86_64",
        libc="glibc",
        libc_version="2.36",
        container_image_id=image,
    )


def metadata():
    return {
        "info": {"name": "example", "version": "1", "requires_python": ">=3.12.10"},
        "urls": [
            {
                "packagetype": "bdist_wheel",
                "filename": "example-1-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/example.whl",
                "requires_python": ">=3.12.10",
            }
        ],
    }


def test_patch_requirements_apply_to_metadata_assets_and_dependency_markers():
    package = metadata()
    with pytest.raises(EnvironmentLockError):
        validate_package(package, "example", "1")
    with pytest.raises(EnvironmentLockError):
        choose_wheel(package)
    validate_package(package, "example", "1", full_version="3.12.15")
    assert choose_wheel(package, full_version="3.12.15")["filename"].endswith(".whl")
    graph = {"example": {"info": {"requires_dist": ["helper; python_full_version >= '3.12.10'"]}}}
    with pytest.raises(EnvironmentLockError, match="helper"):
        validate_closure(graph, {"example": "1"}, full_version="3.12.15")
    graph["helper"] = {"info": {"requires_dist": []}}
    validate_closure(graph, {"example": "1", "helper": "1"}, full_version="3.12.15")
    graph["example"]["info"]["requires_dist"] = ["absent; implementation_version < '3.12.10'"]
    validate_closure(graph, {"example": "1", "helper": "1"}, full_version="3.12.15")


def test_optional_identity_preserves_old_serialized_locks_and_freezes_new_ones():
    raw = '{"version":"environment.lock/v1","python":"3.12","platform":"linux_x86_64","wheels":[]}'
    assert EnvironmentLock.model_validate_json(raw).model_dump_json() == raw
    assert json.loads(EnvironmentSpec().model_dump_json()) == {"requirements": []}
    lock = EnvironmentLock(interpreter=identity())
    assert EnvironmentLock.model_validate_json(lock.model_dump_json()) == lock
    with pytest.raises(ValueError):
        EnvironmentLock(python="3.13", interpreter=identity())
    with pytest.raises(ValueError):
        identity().python_full_version = "3.12.0"


@pytest.mark.parametrize(
    "requested", [">=3.12.10,<3.13", "==3.12.15", "!=3.12.0,!=3.12.1", "~=3.12.15"]
)
def test_planning_defers_possible_patch_constraints_to_actual_container(settings, requested):
    assert possible_python("3.12", SpecifierSet(requested))
    assert runtime_for_intent(settings, "用 Python" + requested + "执行") is None
    if "!=" not in requested:
        assert not possible_python("3.11", SpecifierSet(requested))


async def test_uv_receives_actual_complete_version_and_keeps_its_identity(monkeypatch):
    import asyncio
    from pathlib import Path

    resolver = DependencyResolver().for_interpreter(identity())
    assert resolver.python == "3.12"
    assert resolver.for_python("3.13").interpreter is None
    observed = []

    async def create_process(*arguments, **options):
        observed.extend(arguments)
        await asyncio.to_thread(
            Path(arguments[arguments.index("--output-file") + 1]).write_text,
            "example==1\n",
            encoding="utf-8",
        )
        process = AsyncMock()
        process.returncode = 0
        process.stdout, process.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        process.stdout.feed_eof()
        process.stderr.feed_eof()
        process.wait.return_value = 0
        return process

    monkeypatch.setattr("openresearch.sources.dependency_resolution.shutil.which", lambda _: "uv")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    spec = await resolver.resolve(["example"])
    assert observed[observed.index("--python-version") + 1] == "3.12.15"
    assert spec.interpreter == identity()
    assert spec.requirements == ["example==1"]


async def test_preparation_records_probe_failure_without_attempting_resolution(settings):
    preparation = EnvironmentPreparation(settings)
    preparation.resolver.resolve = AsyncMock(side_effect=AssertionError("must not resolve"))
    probe = AsyncMock(side_effect=ContractError("unsupported interpreter"))
    result = await preparation.run("run", ["example"], [], 2, probe=probe)
    assert not result.report.success
    assert result.report.attempts[0].phase == "probe"
    assert result.report.attempts[0].error == "unsupported interpreter"
    assert not result.report.decisions
    preparation.resolver.resolve.assert_not_awaited()


async def test_probe_cannot_silently_replace_the_preparation_target(settings):
    result = await EnvironmentPreparation(settings).run(
        "run", [], [], 2, probe=AsyncMock(return_value=identity("3.13.16"))
    )
    assert not result.report.success and not result.report.decisions
    assert "differs from the preparation target" in result.report.attempts[0].error


async def test_changed_identity_between_design_and_locking_cannot_be_repaired(settings):
    preparation = EnvironmentPreparation(settings)
    before = identity(image="sha256:" + "a" * 64)
    after = identity(image="sha256:" + "b" * 64)
    result = await preparation.run(
        "run",
        [],
        [],
        2,
        initial_spec=EnvironmentSpec(interpreter=before),
        lock_wheels=True,
        probe=AsyncMock(return_value=after),
    )
    assert not result.report.success
    assert result.report.attempts[0].interpreter == after
    assert "changed" in result.report.attempts[0].error
    assert not result.report.decisions


async def test_image_drift_is_rejected_before_installing_or_running_experiment(settings):
    session = AsyncMock()
    session.runtime_profile = None
    session.adapter.image_identity.return_value = "sha256:" + "b" * 64
    lock = EnvironmentLock(interpreter=identity(image="sha256:" + "a" * 64))
    with pytest.raises(SandboxError, match="image changed"):
        await install_environment(session, lock, settings)
    session.put_files.assert_not_awaited()
    session.execute.assert_not_awaited()


async def test_same_minor_recovery_uses_probed_patch_versions_and_preserves_requests(settings):
    from openresearch.models.runtime_profiles import RuntimeProfile

    settings.runtime_profiles = {
        "old": RuntimeProfile(image="python:3.12.1-slim-bookworm", python="3.12"),
        "new": RuntimeProfile(image="python:3.12.15-slim-bookworm", python="3.12"),
    }

    async def probe(binding):
        return identity("3.12.15" if binding and binding.profile == "new" else "3.12.1")

    evidence = [{"name": "example", "version": "1", "requires_python": ">=3.12.10"}]
    binding = await compatible_interpreter(
        settings, "保留example==1", None, evidence, ["example==1"], probe
    )
    assert binding.profile == "new"
    assert (
        await compatible_interpreter(
            settings, "不要切换Python", None, evidence, ["example==1"], probe
        )
        is None
    )
    assert (
        await compatible_interpreter(settings, "run", None, evidence, ["example<1"], probe) is None
    )
