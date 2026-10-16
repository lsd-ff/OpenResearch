"""该程序由 DocumentAgent 作为源码送入容器，禁止在 API 进程直接解析 PDF。"""

import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader

ROOT = Path("/tmp/openresearch")


def pdf_pages(path: Path, limits: dict[str, int]) -> tuple[str, list[dict[str, Any]]]:
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted:
        raise ValueError("encrypted PDFs require an unencrypted source document")
    if not 1 <= len(reader.pages) <= limits["max_pages"]:
        raise ValueError("PDF page count exceeds configured limit")
    pages: list[dict[str, Any]] = []
    total = 0
    for number, page in enumerate(reader.pages, 1):
        content = page.get_contents()
        if content is not None and len(content.get_data()) > 2 * 1024 * 1024:
            raise ValueError("PDF page content stream exceeds configured limit")
        text = page.extract_text() or ""
        total += len(text.encode("utf-8"))
        if total > limits["max_text_bytes"]:
            raise ValueError("PDF extracted text exceeds configured limit")
        pages.append({"number": number, "text": text, "needs_ocr": not text.strip()})
    title = str(reader.metadata.title or "")[:500] if reader.metadata else ""
    return title, pages


def extract_source(source: dict[str, Any], limits: dict[str, int]) -> dict[str, Any]:
    path = ROOT / "inputs" / source["id"]
    if source["content_type"] == "application/pdf":
        title, pages = pdf_pages(path, limits)
    else:
        raw = path.read_bytes()
        if len(raw) > limits["max_text_bytes"]:
            raise ValueError("text document exceeds configured extraction limit")
        text = raw.decode("utf-8-sig")
        title, pages = source["name"], [{"number": 1, "text": text, "needs_ocr": not text.strip()}]
    return {"source": source, "title": title, "pages": pages}


def main() -> None:
    configuration = json.loads((ROOT / "document_input.json").read_text(encoding="utf-8"))
    documents = [
        extract_source(source, configuration["limits"]) for source in configuration["sources"]
    ]
    result = {"version": "paper.corpus/v1", "documents": documents}
    payload = json.dumps(result, ensure_ascii=False)
    if len(payload.encode("utf-8")) > configuration["limits"]["max_result_bytes"]:
        raise ValueError("document corpus exceeds configured artifact limit")
    (ROOT / "parsed_paper.json").write_text(payload, encoding="utf-8")
    print(
        json.dumps(
            {"documents": len(documents), "pages": sum(len(item["pages"]) for item in documents)}
        )
    )


if __name__ == "__main__":
    main()
