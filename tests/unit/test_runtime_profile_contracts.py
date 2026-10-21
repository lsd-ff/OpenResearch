"""命名运行时的配置、协议确认和目标版本契约。"""

import json
import time

import httpx
import pytest
from conftest import FakeSandbox
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openresearch.config import Settings
from openresearch.errors import ContractError, EnvironmentLockError, SandboxError
from openresearch.models.resources import ManagedSandbox
from openresearch.models.runtime_profiles import RuntimeProfile
from openresearch.models.sources import EnvironmentLock
from openresearch.sandbox.fallback import FallbackSandbox
from openresearch.sandbox.http import HttpSandbox
from openresearch.sandbox.opensandbox import OpenSandbox
from openresearch.sandbox.profiles import profile_fingerprint, selected_profile
from openresearch.sandbox.service import create_sandbox_app
from openresearch.sources.stdlib import is_stdlib, stdlib_names
from openresearch.sources.wheels import choose_wheel, validate_closure, validate_package


def profile():
    return RuntimeProfile(image="python:3.13-slim-bookworm", python="3.13", memory_mb=256)


def test_profile_fingerprint_survives_configuration_json_round_trip():
    value = profile()
    copied = RuntimeProfile.model_validate_json(value.model_dump_json())
    assert profile_fingerprint(value) == profile_fingerprint(copied)


@pytest.mark.parametrize(
    "image", ["https://registry/image", "python --privileged", "user:secret@registry/image"]
)
def test_runtime_profiles_cannot_embed_commands_urls_or_credentials(image):
    with pytest.raises(ValidationError):
        RuntimeProfile(image=image, python="3.13")


def test_unknown_profile_is_rejected_and_null_configuration_keeps_legacy_defaults(settings):
    assert selected_profile(settings, None) is None
    with pytest.raises(ContractError, match="not allowed"):
        selected_profile(settings, "unregistered")
    assert Settings(_env_file=None, runtime_profiles=None).runtime_profiles == {}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, runtime_profiles={"../escape": profile()})
    assert EnvironmentLock().model_dump_json() == (
        '{"version":"environment.lock/v1","python":"3.12","platform":"linux_x86_64","wheels":[]}'
    )


class NamedSandbox(FakeSandbox):
    def __init__(self):
        super().__init__()
        self.names = []

    async def create(self, plan_id, execution_id, runtime_profile=None):
        self.names.append(runtime_profile)
        return await super().create(plan_id, execution_id)

    async def inventory(self):
        return [
            ManagedSandbox(key, "b" * 32, time.time())
            for key in self.created
            if key not in self.closed
        ]


@pytest.mark.parametrize("mismatch", [False, True])
async def test_http_service_must_confirm_the_exact_registered_profile(settings, mismatch):
    settings.runtime_profiles = {"cpu13": profile()}
    service = (
        settings.model_copy(
            update={
                "runtime_profiles": {
                    "cpu13": RuntimeProfile(image="python:3.12-slim", python="3.12")
                }
            }
        )
        if mismatch
        else settings
    )
    native = NamedSandbox()
    app = create_sandbox_app(service, FallbackSandbox(native))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://runtime")
    adapter = HttpSandbox(settings, client)
    try:
        if mismatch:
            with pytest.raises(SandboxError, match="did not confirm"):
                await adapter.create("a" * 32, "b" * 32, "cpu13")
            assert native.created == native.closed
        else:
            identifier = await adapter.create("a" * 32, "b" * 32, "cpu13")
            assert native.names == ["cpu13"]
            metadata = (await client.get("/api/v1/runtime-profiles")).json()
            assert metadata["cpu13"]["sha256"] == profile_fingerprint(profile())
            await adapter.close(identifier)
    finally:
        await adapter.shutdown()


async def test_legacy_service_cannot_silently_ignore_a_named_profile(settings):
    settings.runtime_profiles = {"cpu13": profile()}
    paths = []

    def request(value):
        paths.append(value.method)
        if value.method == "POST":
            assert json.loads(value.content)["runtime_profile"] == "cpu13"
            return httpx.Response(200, json={"sandbox_id": "dk-old"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url="http://old")
    adapter = HttpSandbox(settings, client)
    try:
        with pytest.raises(SandboxError, match="did not confirm"):
            await adapter.create("a" * 32, "b" * 32, "cpu13")
        assert paths == ["POST", "DELETE"]
    finally:
        await adapter.shutdown()


def test_unregistered_profile_and_image_overrides_never_reach_the_engine(settings):
    settings.runtime_profiles = {"cpu13": profile()}
    native = NamedSandbox()
    with TestClient(create_sandbox_app(settings, FallbackSandbox(native))) as client:
        assert (
            client.post("/api/v1/sandboxes", json={"runtime_profile": "unknown"}).status_code >= 400
        )
        assert (
            client.post(
                "/api/v1/sandboxes", json={"runtime_profile": "cpu13", "image": "arbitrary"}
            ).status_code
            == 422
        )
        assert not native.created


def test_wheel_tags_requires_python_and_markers_use_the_same_target_version():
    metadata = {
        "info": {"name": "probe", "version": "1", "requires_python": ">=3.13"},
        "urls": [
            {
                "filename": f"probe-1-cp{abi}-cp{abi}-manylinux_2_17_x86_64.whl",
                "url": "https://files.pythonhosted.org/probe.whl",
                "packagetype": "bdist_wheel",
            }
            for abi in (311, 313)
        ],
    }
    with pytest.raises(EnvironmentLockError):
        validate_package(metadata, "probe", "1", "3.12")
    validate_package(metadata, "probe", "1", "3.13")
    assert "cp313-cp313" in choose_wheel(metadata, "3.13")["filename"]
    conditional = {"probe": {"info": {"requires_dist": ["old-only; python_version < '3.13'"]}}}
    validate_closure(conditional, {"probe": "1"}, "3.13")
    with pytest.raises(EnvironmentLockError, match="old-only"):
        validate_closure(conditional, {"probe": "1"}, "3.12")


def test_stdlib_detection_is_bound_to_actual_targets_instead_of_the_host_version():
    assert "cgi" in stdlib_names("3.12") and "cgi" not in stdlib_names("3.13")
    assert all("json" in stdlib_names(version) for version in ("3.11", "3.12", "3.13"))
    assert is_stdlib("cgi") and is_stdlib("JSON") and not is_stdlib("PyYAML")


@pytest.mark.parametrize("gpu", [False, True])
async def test_opensandbox_preserves_cpu_profiles_and_rejects_unsupported_gpu(settings, gpu):
    settings.runtime_profiles = {"named": profile().model_copy(update={"gpu": gpu, "cpus": 2.5})}
    requests = []

    def request(value):
        requests.append(value)
        if value.method == "POST":
            body = json.loads(value.content)
            assert body["image"]["uri"] == profile().image
            assert body["resourceLimits"] == {"cpu": "2.5", "memory": "268435456"}
            assert body["networkPolicy"] == {"defaultAction": "deny", "egress": []}
            return httpx.Response(200, json={"id": "profile-cpu"})
        return httpx.Response(200, json={"status": {"state": "Running"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url="http://os")
    adapter = OpenSandbox(settings, client)
    try:
        if gpu:
            with pytest.raises(SandboxError, match="cannot guarantee GPU"):
                await adapter.create("a" * 32, "b" * 32, "named")
            assert not requests
        else:
            assert await adapter.create("a" * 32, "b" * 32, "named") == "profile-cpu"
    finally:
        await adapter.shutdown()
