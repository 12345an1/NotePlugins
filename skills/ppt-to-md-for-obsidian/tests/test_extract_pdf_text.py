from pathlib import Path

import pytest

from scripts import extract_pdf_text
from scripts.extract_pdf_text import LOW_COVERAGE_WARNING, extract_pdf, extract_pdf_result


def write_blank_pdf(path: Path) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Resources << >> /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n".encode("ascii"))
        chunks.append(obj)
        chunks.append(b"\nendobj\n")
    xref_offset = sum(len(chunk) for chunk in chunks)
    chunks.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    chunks.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        chunks.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    chunks.append(
        (
            "trailer\n"
            f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            "startxref\n"
            f"{xref_offset}\n"
            "%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(b"".join(chunks))


def test_extract_pdf_handles_blank_pdf(tmp_path: Path):
    pdf = tmp_path / "blank.pdf"
    write_blank_pdf(pdf)

    output = extract_pdf(pdf)

    assert "# Extracted PDF Text: blank.pdf" in output
    assert "- Backend:" in output
    assert "- Pages: 1" in output
    assert "- Empty text pages: 1" in output
    assert "- Low coverage: true" in output
    assert LOW_COVERAGE_WARNING in output
    assert "## Page 1" in output
    assert "[No extractable text]" in output


def test_extract_pdf_falls_back_when_first_backend_is_all_empty(monkeypatch, tmp_path: Path):
    pdf = tmp_path / "slides.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    calls: list[str] = []

    def empty_pypdf(source: bytes) -> list[str]:
        assert source == b"%PDF-1.4\n"
        calls.append("pypdf")
        return ["", ""]

    def useful_pdfplumber(source: bytes) -> list[str]:
        assert source == b"%PDF-1.4\n"
        calls.append("pdfplumber")
        return [
            "This page has enough extracted course text to pass the coverage heuristic.",
            "This second page also has enough extracted text for the fallback backend.",
        ]

    def unused_pdftotext(source: bytes) -> list[str]:
        calls.append("pdftotext")
        return ["should not be used"]

    monkeypatch.setattr(extract_pdf_text, "extract_with_pypdf", empty_pypdf)
    monkeypatch.setattr(extract_pdf_text, "extract_with_pdfplumber", useful_pdfplumber)
    monkeypatch.setattr(extract_pdf_text, "extract_with_pdftotext", unused_pdftotext)

    result = extract_pdf_result(pdf)
    output = result.markdown

    assert calls == ["pypdf", "pdfplumber"]
    assert "- Backend: `pdfplumber`" in output
    assert "- Pages: 2" in output
    assert "- Empty text pages: 0" in output
    assert "- Low coverage: false" in output
    assert LOW_COVERAGE_WARNING not in output
    assert result.low_coverage is False
    assert result.backend == "pdfplumber"
    assert result.empty_pages == 0
    assert result.char_count > 0
    assert "enough extracted course text" in output


def test_extract_pdf_warns_when_all_backends_are_low_coverage(monkeypatch, tmp_path: Path):
    pdf = tmp_path / "image_only.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    calls: list[str] = []

    def empty_pypdf(source: bytes) -> list[str]:
        calls.append("pypdf")
        return ["", ""]

    def low_pdfplumber(source: bytes) -> list[str]:
        calls.append("pdfplumber")
        return ["scan", ""]

    def low_pdftotext(source: bytes) -> list[str]:
        calls.append("pdftotext")
        return ["ocr?", ""]

    monkeypatch.setattr(extract_pdf_text, "extract_with_pypdf", empty_pypdf)
    monkeypatch.setattr(extract_pdf_text, "extract_with_pdfplumber", low_pdfplumber)
    monkeypatch.setattr(extract_pdf_text, "extract_with_pdftotext", low_pdftotext)

    result = extract_pdf_result(pdf)

    assert calls == ["pypdf", "pdfplumber", "pdftotext"]
    assert result.low_coverage is True
    assert result.backend == "pdfplumber"
    assert result.empty_pages == 1
    assert result.char_count == 4
    assert LOW_COVERAGE_WARNING in result.markdown
    assert "- Low coverage: true" in result.markdown


def test_low_coverage_accounts_for_page_distribution_not_only_total_characters():
    result = extract_pdf_text.PdfBackendResult(
        name="test",
        pages=["dense text " * 200, "", "", "", "", "", "", "", "", ""],
    )

    assert result.text_char_count > result.page_count * extract_pdf_text.MIN_TEXT_CHARS_PER_PAGE
    assert extract_pdf_text.low_text_coverage(result) is True


def test_choose_backend_rejects_page_budget_overflow(monkeypatch) -> None:
    monkeypatch.setattr(extract_pdf_text, "MAX_PDF_PAGES", 2)
    monkeypatch.setattr(extract_pdf_text, "extract_with_pypdf", lambda _source: ["a", "b", "c"])
    monkeypatch.setattr(extract_pdf_text, "extract_with_pdfplumber", lambda _source: ["a", "b", "c"])
    monkeypatch.setattr(extract_pdf_text, "extract_with_pdftotext", lambda _source: ["a", "b", "c"])

    with pytest.raises(extract_pdf_text.PdfExtractionError, match="page count exceeds limit"):
        extract_pdf_text.choose_backend(b"%PDF-1.4\n")


def test_choose_backend_rejects_text_budget_overflow(monkeypatch) -> None:
    monkeypatch.setattr(extract_pdf_text, "MAX_EXTRACTED_TEXT_CHARS", 4)
    monkeypatch.setattr(extract_pdf_text, "extract_with_pypdf", lambda _source: ["12345"])
    monkeypatch.setattr(extract_pdf_text, "extract_with_pdfplumber", lambda _source: ["12345"])
    monkeypatch.setattr(extract_pdf_text, "extract_with_pdftotext", lambda _source: ["12345"])

    with pytest.raises(extract_pdf_text.PdfExtractionError, match="text exceeds limit"):
        extract_pdf_text.choose_backend(b"%PDF-1.4\n")


@pytest.mark.parametrize("kind", ["leaf", "ancestor", "broken"])
def test_extract_pdf_rejects_symlinked_input(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "real" / "lecture.pdf"
    target.parent.mkdir()
    write_blank_pdf(target)
    if kind == "leaf":
        source = tmp_path / "lecture-link.pdf"
        source.symlink_to(target)
    elif kind == "ancestor":
        alias = tmp_path / "real-link"
        alias.symlink_to(target.parent, target_is_directory=True)
        source = alias / target.name
    else:
        source = tmp_path / "broken.pdf"
        source.symlink_to(tmp_path / "missing.pdf")

    with pytest.raises(extract_pdf_text.PdfExtractionError, match="symlink|does not exist"):
        extract_pdf_result(source)
