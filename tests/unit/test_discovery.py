import pytest

from openresearch.errors import ContractError
from openresearch.sources.discovery import arxiv_pdf_url, parse_arxiv


@pytest.mark.parametrize(
    "url",
    [
        "http://arxiv.org/pdf/1706.03762",
        "https://localhost/a.pdf",
        "https://arxiv.org.evil/pdf/1706.03762",
        "https://arxiv.org@127.0.0.1/pdf/1706.03762",
    ],
)
def test_remote_pdf_does_not_allow_arbitrary_hosts(url) -> None:
    with pytest.raises(ContractError):
        arxiv_pdf_url(url)


def test_arxiv_metadata_preserves_source_and_version() -> None:
    raw = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>http://arxiv.org/abs/1706.03762v7</id><title>Attention Is All You Need</title>
    <summary>Source metadata</summary><published>2017-06-12</published>
    <author><name>Original Author</name></author></entry></feed>"""
    result = parse_arxiv(raw, 5)[0]
    assert result.pdf_url == "https://arxiv.org/pdf/1706.03762v7"
    assert result.authors == ["Original Author"]
    with pytest.raises(ContractError):
        parse_arxiv(b"<!DOCTYPE feed><feed/>", 5)
