import hashlib
import io
import json
import tarfile

import httpx
import pytest

from openresearch.errors import ContractError
from openresearch.models.sources import RepositorySpec
from openresearch.sources.blobs import SourceBlobs
from openresearch.sources.github import GitHubSource


@pytest.fixture(scope="module")
def sources():
    # 明确的归档功能夹具，不作为论文源码或执行成功的证据。
    return {
        "AnnotatedTransformer.ipynb": json.dumps({"cells": [], "padding": "x" * 7526000}).encode(),
        "docs/index.html": b"<html>" + b"x" * 9453000 + b"</html>",
        "model.py": b"class EncoderDecoder:\n    pass\n",
    }


def transport(sources):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for path, raw in sources.items():
            item = tarfile.TarInfo("root/" + path)
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
    return httpx.MockTransport(lambda _: httpx.Response(200, content=stream.getvalue()))


async def test_default_budgets_preserve_large_files_and_original_sha(settings, sources, tmp_path):
    snapshot, actual = await GitHubSource(settings, transport(sources)).fetch(
        RepositorySpec(url="https://github.com/example/large", ref="a" * 40)
    )
    assert actual == sources and len(snapshot.files) == len(sources)
    blobs = SourceBlobs(tmp_path / "sources", settings.source_max_file_bytes)
    for file in snapshot.files:
        assert file.size == len(sources[file.path])
        assert file.sha256 == hashlib.sha256(sources[file.path]).hexdigest()
        assert blobs.write(actual[file.path]) == file.sha256
        assert blobs.read(file.sha256, file.size) == sources[file.path]


async def test_explicit_smaller_file_budget_is_still_enforced(settings, sources):
    configured = settings.model_copy(update={"source_max_file_bytes": 4 * 1024 * 1024})
    with pytest.raises(ContractError, match="file limit"):
        await GitHubSource(configured, transport(sources)).fetch(
            RepositorySpec(url="https://github.com/example/large", ref="a" * 40)
        )


@pytest.mark.parametrize(
    "budget,value", [("source_max_bytes", 8 * 1024 * 1024), ("source_max_files", 1)]
)
async def test_total_and_file_count_budgets_remain_binding(settings, sources, budget, value):
    configured = settings.model_copy(update={budget: value})
    with pytest.raises(ContractError, match="extraction budget"):
        await GitHubSource(configured, transport(sources)).fetch(
            RepositorySpec(url="https://github.com/example/large", ref="a" * 40)
        )
