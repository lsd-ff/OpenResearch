import io
import tarfile

import pytest

from openresearch.errors import ContractError
from openresearch.sandbox.file_transfer import archive_files, relative_path


@pytest.mark.parametrize(
    "path", ["../secret", "/etc/passwd", "a/../../b", "C:\\file", ".", "x\x00y"]
)
def test_paths_cannot_escape_container_workspace(path: str) -> None:
    with pytest.raises(ContractError):
        relative_path(path)


def test_archive_contains_owned_directories_and_exact_input_bytes() -> None:
    raw = archive_files({"inputs/data.txt": b"example", "vendor/pkg/__init__.py": b""})
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        file = archive.extractfile("openresearch/inputs/data.txt")
        assert file is not None and file.read() == b"example"
        for item in archive.getmembers():
            assert item.uid == item.gid == 65534
            assert not item.issym() and not item.islnk()
