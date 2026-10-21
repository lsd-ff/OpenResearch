import json

import httpx
import pytest
from pydantic import ValidationError

from openresearch.models.sources import EnvironmentSpec


def test_dependency_closure_supports_real_framework_sizes_but_remains_bounded():
    assert (
        len(
            EnvironmentSpec(
                requirements=[f"dependency-{index}==1.0" for index in range(62)]
            ).requirements
        )
        == 62
    )
    with pytest.raises(ValidationError):
        EnvironmentSpec(requirements=[f"dependency-{index}==1.0" for index in range(129)])


async def test_package_metadata_is_pinned_to_requested_source_and_has_raw_checksum(settings):
    from openresearch.sources.packages import PackageSource

    payload = {
        "info": {
            "name": "langchain-core",
            "version": "1.0",
            "summary": "Actual summary",
            "description": "Actual source description",
            "project_urls": {"Source": "https://github.com/langchain-ai/langchain"},
        }
    }
    raw = json.dumps(payload).encode()

    def provider(request):
        assert str(request.url) == "https://pypi.org/pypi/langchain-core/1.0/json"
        return httpx.Response(200, content=raw)

    source = PackageSource(settings, httpx.MockTransport(provider))
    result = await source.describe("langchain_core", "1.0")
    import hashlib

    assert result.name == "langchain-core" and result.version == "1.0"
    assert result.metadata_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.description == "Actual source description"


async def test_package_metadata_cannot_substitute_another_library(settings):
    from openresearch.errors import ContractError
    from openresearch.sources.packages import PackageSource

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "info": {"name": "another-library", "version": "1.0"},
            },
        )
    )
    with pytest.raises(ContractError, match="identity"):
        await PackageSource(settings, transport).describe("langchain-core", "1.0")
