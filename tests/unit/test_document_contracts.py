import pytest

from openresearch.models.documents import ParsedDocument, ParsedPage, SourceDocument


def test_extracted_pages_must_have_contiguous_page_numbers() -> None:
    source = SourceDocument(
        id="upload", name="paper.pdf", sha256="a" * 64, content_type="application/pdf"
    )
    with pytest.raises(ValueError, match="page numbers"):
        ParsedDocument(source=source, pages=[ParsedPage(number=2, text="Text")])


def test_empty_page_is_explicitly_marked_for_ocr() -> None:
    page = ParsedPage(number=1, text="")
    assert page.needs_ocr
