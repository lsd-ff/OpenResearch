import hashlib
import json

import httpx
import pytest
from conftest import make_repository_archive as make_archive

from openresearch.errors import ContractError
from openresearch.models.sources import EnvironmentSpec, RepositorySpec
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.github import GitHubSource
from openresearch.sources.wheels import WheelSource


async def test_github_download_uses_resolved_sha(settings) -> None:
    commit = "a" * 40
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"sha": commit})
        assert request.url.path.endswith(commit)
        return httpx.Response(200, content=make_archive())

    source = GitHubSource(settings, httpx.MockTransport(handle))
    snapshot, files = await source.fetch(
        RepositorySpec(url="https://github.com/example/repo", ref="main")
    )
    assert snapshot.commit == commit
    assert snapshot.files[0].sha256 == hashlib.sha256(files["main.py"]).hexdigest()
    assert len(calls) == 2


async def test_repository_download_size_is_bounded(settings) -> None:
    settings.source_max_archive_bytes = 10
    transport = httpx.MockTransport(
        lambda request: (
            httpx.Response(200, json={"sha": "a" * 40})
            if request.url.host == "api.github.com"
            else httpx.Response(200, content=b"x" * 11)
        )
    )
    with pytest.raises(ContractError, match="size limit"):
        await GitHubSource(settings, transport).fetch(RepositorySpec(url="https://github.com/a/b"))


@pytest.mark.parametrize("failure", ["hash", "closure"])
async def test_package_checksum_and_dependency_closure_are_verified(settings, failure: str) -> None:
    raw = b"wheel bytes"
    metadata = {
        "info": {
            "name": "example",
            "version": "1.0",
            "requires_dist": ["missing>=1"] if failure == "closure" else [],
        },
        "urls": [
            {
                "filename": "example-1.0-py3-none-any.whl",
                "packagetype": "bdist_wheel",
                "url": "https://files.pythonhosted.org/example.whl",
                "digests": {"sha256": "a" * 64},
            }
        ],
    }
    transport = httpx.MockTransport(
        lambda request: (
            httpx.Response(200, content=json.dumps(metadata).encode())
            if request.url.host == "pypi.org"
            else httpx.Response(200, content=raw)
        )
    )
    with pytest.raises(ContractError, match="checksum" if failure == "hash" else "exact pin"):
        await WheelSource(settings, transport).fetch(EnvironmentSpec(requirements=["example==1.0"]))


@pytest.mark.parametrize(
    "info",
    [
        {"name": "different", "version": "1.0"},
        {"name": "example", "version": "2.0"},
        {"name": "example", "version": "1.0", "requires_python": ">=3.13"},
    ],
)
async def test_incompatible_package_metadata_is_rejected_before_download(settings, info) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"info": info, "urls": []})
    )
    with pytest.raises(ContractError):
        await WheelSource(settings, transport).fetch(EnvironmentSpec(requirements=["example==1.0"]))


@pytest.mark.parametrize("corrupted", [False, True])
async def test_wheel_cache_revalidates_metadata_and_actual_content(settings, corrupted):
    raw = b"actual immutable wheel bytes"
    blobs = SourceBlobs(settings.data_dir / "wheels", settings.dependency_max_bytes)
    digest = blobs.write(raw)
    if corrupted:
        (blobs.root / digest[:2] / digest).write_bytes(b"x" * len(raw))
    calls = []

    def handle(request):
        calls.append(request.url.host)
        assert request.url.host == "pypi.org", (
            "a validated cached wheel must not be downloaded again"
        )
        return httpx.Response(
            200,
            json={
                "info": {"name": "example", "version": "1.0"},
                "urls": [
                    {
                        "filename": "example-1.0-py3-none-any.whl",
                        "packagetype": "bdist_wheel",
                        "url": "https://files.pythonhosted.org/example.whl",
                        "size": len(raw),
                        "digests": {"sha256": digest},
                    }
                ],
            },
        )

    source = WheelSource(settings, httpx.MockTransport(handle))
    specification = EnvironmentSpec(requirements=["example==1.0"])
    if corrupted:
        with pytest.raises(ContractError, match="checksum"):
            await source.fetch(specification)
    else:
        lock, files = await source.fetch(specification)
        assert lock.wheels[0].sha256 == digest and list(files.values()) == [raw]
    assert calls == ["pypi.org"]
