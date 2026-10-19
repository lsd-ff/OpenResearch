import pytest

from openresearch.errors import ContractError
from openresearch.sources.git_refs import advertised_head


def packet(value: bytes) -> bytes:
    return f"{len(value) + 4:04x}".encode() + value


SERVICE = packet(b"# service=git-upload-pack\n") + b"0000"
HEAD = packet(b"a" * 40 + b" HEAD\0symref=HEAD:refs/heads/main multi_ack\n")


def test_git_head_comes_from_the_actual_advertisement():
    branch = packet(b"b" * 40 + b" refs/heads/develop\n")
    assert advertised_head(SERVICE + HEAD + branch + b"0000") == "a" * 40


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"<html>error</html>",
        b"zzzz",
        SERVICE + HEAD,
        SERVICE + HEAD + b"0000junk",
        SERVICE + HEAD + HEAD + b"0000",
        SERVICE + packet(b"0" * 40 + b" HEAD\0capabilities\n") + b"0000",
        SERVICE + packet(b"b" * 40 + b" refs/heads/main\n") + b"0000",
        packet(b"# service=git-receive-pack\n") + b"0000" + HEAD + b"0000",
        SERVICE + HEAD + b"00010000",
        SERVICE + HEAD + b"ffffunfinished",
    ],
)
def test_invalid_incomplete_or_ambiguous_git_refs_are_rejected(raw):
    with pytest.raises(ContractError):
        advertised_head(raw)
