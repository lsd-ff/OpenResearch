import io
import tarfile

import pytest

from openresearch.errors import ContractError
from openresearch.sandbox.file_transfer import archive_files
from openresearch.sandbox.input_bundle import unpack_bundle


def test_actual_binary_bundle_roundtrip(settings):
    files = {"wheels/test.whl": bytes(range(256)) * 8192, "repository/adapter.py": b"print(42)"}
    assert unpack_bundle(archive_files(files), settings) == files


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("openresearch/link", tarfile.SYMTYPE),
        ("foreign/input", tarfile.REGTYPE),
    ],
)
def test_untrusted_bundle_entries_are_rejected(settings, name, kind):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        entry.linkname = "/etc/passwd"
        archive.addfile(entry)
    with pytest.raises(ContractError):
        unpack_bundle(raw.getvalue(), settings)


def test_bundle_total_size_is_checked_before_reading_contents(settings):
    settings.source_max_bytes = settings.dependency_max_bytes = 1024
    with pytest.raises(ContractError, match="oversized"):
        unpack_bundle(archive_files({"large.bin": b"x" * 3000}), settings)
