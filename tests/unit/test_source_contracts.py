import tarfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import make_repository_archive as make_archive
from pydantic import ValidationError

from openresearch.errors import ContractError
from openresearch.models.sources import EnvironmentSpec, RepositorySpec
from openresearch.sources.archive import ArchiveLimits, unpack_repository
from openresearch.sources.blobs import SourceBlobs


@pytest.mark.parametrize("name", ["/root/a", "root/../a", "root/a\\b", "root/C:/a"])
def test_archive_rejects_paths_that_escape_workspace(name: str) -> None:
    with pytest.raises(ContractError):
        unpack_repository(make_archive(name), ArchiveLimits(1024, 1024, 10))


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE])
def test_archive_rejects_links_and_devices(kind: bytes) -> None:
    with pytest.raises(ContractError):
        unpack_repository(make_archive(kind=kind), ArchiveLimits(1024, 1024, 10))


def test_archive_bounds_and_normal_file() -> None:
    raw = make_archive()
    assert unpack_repository(raw, ArchiveLimits(1024, 1024, 10)) == {"main.py": b"print(1)"}
    with pytest.raises(ContractError):
        unpack_repository(raw, ArchiveLimits(4, 1024, 10))


@pytest.mark.parametrize("pin", ["numpy", "numpy>=1", "foo @ https://evil/a.whl", "foo==1.*"])
def test_dependency_contract_requires_explicit_pins(pin: str) -> None:
    with pytest.raises(ValidationError):
        EnvironmentSpec(requirements=[pin])


@pytest.mark.parametrize(
    "url", ["http://github.com/a/b", "https://localhost/a/b", "https://github.com/a/b/tree/main"]
)
def test_repository_contract_limits_remote_hosts(url: str) -> None:
    with pytest.raises(ValidationError):
        RepositorySpec(url=url)


def test_blob_publication_is_atomic_and_hash_checked(tmp_path) -> None:
    store = SourceBlobs(tmp_path, 1024)
    content = b"source code"
    with ThreadPoolExecutor(max_workers=4) as pool:
        digests = list(pool.map(store.write, [content] * 8))
    assert len(set(digests)) == 1
    assert store.read(digests[0], len(content)) == content
    store._path(digests[0]).write_bytes(b"source fail")
    with pytest.raises(ContractError, match="checksum"):
        store.read(digests[0], len(content))
