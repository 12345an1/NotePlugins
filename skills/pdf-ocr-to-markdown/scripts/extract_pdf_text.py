#!/usr/bin/env python3
"""Extract embedded PDF text page-by-page with local Python libraries."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DependencyIssue(RuntimeError):
    """Raised when no usable local PDF text extraction dependency is available."""


@dataclass
class ExtractedPage:
    page: int
    text: str
    metadata: dict[str, Any]
    text_path: str | None = None
    json_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "text": self.text,
            "metadata": self.metadata,
            "text_path": self.text_path,
            "json_path": self.json_path,
        }


def dependency_status() -> dict[str, Any]:
    """Return import availability for supported PDF text extraction libraries."""

    status: dict[str, Any] = {
        "pdfplumber": False,
        "pypdf": False,
        "usable": False,
        "install": "python -m pip install pdfplumber pypdf",
    }
    try:
        import pdfplumber  # noqa: F401

        status["pdfplumber"] = True
    except Exception as exc:  # pragma: no cover - environment-specific
        status["pdfplumber_error"] = str(exc)

    try:
        import pypdf  # noqa: F401

        status["pypdf"] = True
    except Exception as exc:  # pragma: no cover - environment-specific
        status["pypdf_error"] = str(exc)

    status["usable"] = bool(status["pdfplumber"] or status["pypdf"])
    return status


def _write_page(output_dir: Path, page: ExtractedPage) -> ExtractedPage:
    output_dir.mkdir(parents=True, exist_ok=True)
    text_path = output_dir / f"page_{page.page:04d}.txt"
    json_path = output_dir / f"page_{page.page:04d}.json"
    page.text_path = str(text_path)
    page.json_path = str(json_path)
    text_path.write_text(page.text, encoding="utf-8")
    json_path.write_text(
        json.dumps(page.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return page


def _extract_with_pdfplumber(pdf_path: Path, output_dir: Path | None) -> list[ExtractedPage]:
    import pdfplumber

    pages: list[ExtractedPage] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            objects = getattr(page, "objects", {}) or {}
            metadata = {
                "extractor": "pdfplumber",
                "width": float(page.width or 0),
                "height": float(page.height or 0),
                "char_count": len(text),
                "pdf_char_objects": len(getattr(page, "chars", []) or []),
                "images": len(getattr(page, "images", []) or []),
                "lines": len(objects.get("line", []) or []),
                "rectangles": len(objects.get("rect", []) or []),
                "curves": len(objects.get("curve", []) or []),
            }
            extracted = ExtractedPage(page=index, text=text, metadata=metadata)
            if output_dir is not None:
                extracted = _write_page(output_dir, extracted)
            pages.append(extracted)
    return pages


def _extract_with_pypdf(pdf_path: Path, output_dir: Path | None) -> list[ExtractedPage]:
    import pypdf

    reader = pypdf.PdfReader(str(pdf_path))
    pages: list[ExtractedPage] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        media_box = getattr(page, "mediabox", None)
        width = float(media_box.width) if media_box is not None else 0.0
        height = float(media_box.height) if media_box is not None else 0.0
        metadata = {
            "extractor": "pypdf",
            "width": width,
            "height": height,
            "char_count": len(text),
            "pdf_char_objects": None,
            "images": None,
            "lines": None,
            "rectangles": None,
            "curves": None,
        }
        extracted = ExtractedPage(page=index, text=text, metadata=metadata)
        if output_dir is not None:
            extracted = _write_page(output_dir, extracted)
        pages.append(extracted)
    return pages


def extract_pdf_text(pdf_path: str | Path, output_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Extract page text and practical page metadata from a PDF."""

    pdf = Path(pdf_path)
    if not pdf.exists():
        raise FileNotFoundError(str(pdf))

    out = Path(output_dir) if output_dir is not None else None
    status = dependency_status()
    if status["pdfplumber"]:
        pages = _extract_with_pdfplumber(pdf, out)
    elif status["pypdf"]:
        pages = _extract_with_pypdf(pdf, out)
    else:
        raise DependencyIssue(
            "No PDF text extraction dependency is installed. Install with: "
            f"{status['install']}"
        )
    return [page.to_dict() for page in pages]


def count_pdf_pages(pdf_path: str | Path) -> int | None:
    """Best-effort PDF page count for dry-run planning."""

    pdf = Path(pdf_path)
    status = dependency_status()
    if status["pdfplumber"]:
        import pdfplumber

        with pdfplumber.open(str(pdf)) as document:
            return len(document.pages)
    if status["pypdf"]:
        import pypdf

        reader = pypdf.PdfReader(str(pdf))
        return len(reader.pages)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PDF text page-by-page.")
    parser.add_argument("pdf", help="PDF file to inspect")
    parser.add_argument("--out-dir", default=None, help="Directory for page text/json files")
    args = parser.parse_args()

    try:
        pages = extract_pdf_text(args.pdf, args.out_dir)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"ok": True, "pages": pages}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
