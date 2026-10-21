import asyncio
from unittest.mock import AsyncMock
from urllib.parse import unquote, urlsplit

import pytest
from pydantic import SecretStr

from openresearch.agents.research_design import ResearchDesigner
from openresearch.errors import DependencyResolutionError
from openresearch.models.interpreters import InterpreterIdentity
from openresearch.research.dependency_recovery import DependencyRecovery
from openresearch.research.environment_preparation import EnvironmentPreparation
from openresearch.sources.dependency_resolution import DependencyResolver
from openresearch.sources.frameworks import FrameworkSources

PROXY = "http://source-user:network%2Dpassword@proxy.invalid:8080"


@pytest.mark.parametrize("configured", [False, True])
async def test_resolver_only_passes_explicit_source_network_and_redacts_diagnostics(
    monkeypatch, tmp_path, configured
):
    monkeypatch.setenv("HTTPS_PROXY", "http://ambient.invalid:9876")
    monkeypatch.setenv("OPENRESEARCH_LLM_API_KEY", "model-credential-must-stay-out")
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://ambient-index.invalid/simple")
    observed = {}

    async def create(*arguments, **options):
        observed.update(options["env"])
        assert arguments[arguments.index("--default-index") + 1] == "https://pypi.org/simple"
        assert "--no-build" in arguments and "--no-config" in arguments
        process = AsyncMock()
        process.returncode = 1
        process.stdout, process.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        process.stdout.feed_eof()
        diagnostic = "Requires-Python >=3.12; invalid version"
        if configured:
            password = urlsplit(PROXY).password
            diagnostic += f" {PROXY} {password} {unquote(password)}"
        process.stderr.feed_data(diagnostic.encode())
        process.stderr.feed_eof()
        process.wait.return_value = 1
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    resolver = DependencyResolver(source_proxy_url=SecretStr(PROXY if configured else ""))
    with pytest.raises(DependencyResolutionError) as failure:
        await resolver._compile("uv", tmp_path)
    assert observed.get("HTTPS_PROXY") == (PROXY if configured else None)
    assert "OPENRESEARCH_LLM_API_KEY" not in observed and "UV_DEFAULT_INDEX" not in observed
    assert "Requires-Python >=3.12" in failure.value.diagnostic
    assert all(
        value not in failure.value.diagnostic
        for value in (PROXY, "network%2Dpassword", "network-password")
    )


@pytest.mark.parametrize("mode", ["python", "interpreter"])
def test_runtime_clone_preserves_explicit_source_network(mode):
    resolver = DependencyResolver(source_proxy_url=SecretStr(PROXY))
    clone = (
        resolver.for_python("3.13")
        if mode == "python"
        else resolver.for_interpreter(
            InterpreterIdentity(
                python_full_version="3.13.13",
                implementation="CPython",
                system="Linux",
                machine="x86_64",
                libc="glibc",
                libc_version="2.39",
            )
        )
    )
    assert clone.python == "3.13" and resolver.python == "3.12"
    assert clone.source_proxy_url.get_secret_value() == PROXY


@pytest.mark.parametrize("component", ["design", "prepare", "recover", "framework"])
def test_all_dependency_entrypoints_bind_the_configured_source_network(settings, files, component):
    settings.source_proxy_url = SecretStr(PROXY)
    if component == "design":
        resolver = ResearchDesigner(settings, files).dependencies
    else:
        factory = {
            "prepare": EnvironmentPreparation,
            "recover": DependencyRecovery,
            "framework": FrameworkSources,
        }[component]
        resolver = factory(settings).resolver
    assert resolver.source_proxy_url.get_secret_value() == PROXY
