#!/usr/bin/env python3
"""Extract PDF text into Markdown.

Uses pypdf by default and falls back to pdfplumber or the pdftotext CLI when
available. The output is raw source material for note rewriting.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import sys
import tempfile

try:
    from .safe_io import ensure_safe_input_file, read_bytes_no_follow, safe_write_text
    from .run_with_timeout import run_capture
except ImportError:
    from safe_io import ensure_safe_input_file, read_bytes_no_follow, safe_write_text
    from run_with_timeout import run_capture


MIN_TEXT_CHARS_PER_PAGE = 20
MIN_NONEMPTY_PAGE_RATIO = 0.5
LOW_COVERAGE_WARNING = "Warning: low text coverage; source may be scanned/image-only and needs OCR or manual inspection."
MAX_PDF_INPUT_BYTES = 256 * 1024 * 1024
MAX_PDF_PAGES = 10_000
MAX_EXTRACTED_TEXT_CHARS = 64 * 1024 * 1024


class PdfExtractionError(RuntimeError):
    """Raised when no PDF text extraction backend can provide page data."""


def validate_pdf_input(path: Path) -> Path:
    if path.suffix.casefold() != ".pdf":
        raise PdfExtractionError(f"{path}: input must be a .pdf file")
    try:
        safe_path = ensure_safe_input_file(path)
    except (OSError, ValueError) as exc:
        raise PdfExtractionError(f"{path}: {exc}") from exc
    return safe_path


@dataclass(frozen=True)
class PdfBackendResult:
    name: str
    pages: list[str]
    error: str | None = None

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text_char_count(self) -> int:
        return sum(len(page.strip()) for page in self.pages)

    @property
    def empty_page_count(self) -> int:
        return sum(1 for page in self.pages if not page.strip())

    @property
    def nonempty_page_count(self) -> int:
        return self.page_count - self.empty_page_count


@dataclass(frozen=True)
class PdfExtractionResult:
    markdown: str
    backend: str
    low_coverage: bool
    empty_pages: int
    char_count: int
    page_count: int


def extract_with_pypdf(source: bytes) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return []

    reader = PdfReader(BytesIO(source))
    if len(reader.pages) > MAX_PDF_PAGES:
        raise PdfExtractionError(f"pypdf page count exceeds limit ({MAX_PDF_PAGES})")
    pages = []
    char_count = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        char_count += len(text)
        if char_count > MAX_EXTRACTED_TEXT_CHARS:
            raise PdfExtractionError(
                f"pypdf text exceeds limit ({MAX_EXTRACTED_TEXT_CHARS} characters)"
            )
        pages.append(text)
    return pages


def extract_with_pdfplumber(source: bytes) -> list[str]:
    try:
        import pdfplumber
    except ImportError:
        return []

    pages = []
    char_count = 0
    with pdfplumber.open(BytesIO(source)) as pdf:
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise PdfExtractionError(
                f"pdfplumber page count exceeds limit ({MAX_PDF_PAGES})"
            )
        for page in pdf.pages:
            text = page.extract_text() or ""
            char_count += len(text)
            if char_count > MAX_EXTRACTED_TEXT_CHARS:
                raise PdfExtractionError(
                    "pdfplumber text exceeds limit "
                    f"({MAX_EXTRACTED_TEXT_CHARS} characters)"
                )
            pages.append(text)
    return pages


def extract_with_pdftotext(source: bytes) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="solvenotes-pdf-") as temporary:
        source_path = Path(temporary) / "source.pdf"
        source_path.write_bytes(source)
        result = run_capture(
            ["pdftotext", "-layout", str(source_path), "-"],
            60,
            "pdftotext extraction",
            max_stdout_bytes=MAX_EXTRACTED_TEXT_CHARS * 4,
        )
        if result.timed_out or result.returncode == 127:
            return []
        if result.stdout_limit_exceeded:
            raise PdfExtractionError(
                "pdftotext output exceeds byte limit "
                f"({MAX_EXTRACTED_TEXT_CHARS * 4} bytes)"
            )
        if result.returncode != 0:
            return []
        text = result.stdout.decode("utf-8", errors="replace")

    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    pages = [page.strip("\n") for page in pages]
    validate_extracted_pages("pdftotext", pages)
    return pages


def validate_extracted_pages(backend: str, pages: list[str]) -> None:
    if len(pages) > MAX_PDF_PAGES:
        raise PdfExtractionError(
            f"{backend} page count exceeds limit ({MAX_PDF_PAGES})"
        )
    char_count = sum(len(page) for page in pages)
    if char_count > MAX_EXTRACTED_TEXT_CHARS:
        raise PdfExtractionError(
            f"{backend} text exceeds limit ({MAX_EXTRACTED_TEXT_CHARS} characters)"
        )


def low_text_coverage(result: PdfBackendResult) -> bool:
    if not result.pages:
        return True
    if result.text_char_count == 0:
        return True
    if result.nonempty_page_count / result.page_count < MIN_NONEMPTY_PAGE_RATIO:
        return True
    return result.text_char_count < result.page_count * MIN_TEXT_CHARS_PER_PAGE


def backend_sort_key(result: PdfBackendResult) -> tuple[int, int, int]:
    return (result.nonempty_page_count, result.text_char_count, result.page_count)


def choose_backend(source: bytes) -> PdfBackendResult:
    backends = [
        ("pypdf", extract_with_pypdf),
        ("pdfplumber", extract_with_pdfplumber),
        ("pdftotext", extract_with_pdftotext),
    ]
    attempted: list[PdfBackendResult] = []
    best: PdfBackendResult | None = None

    for name, extractor in backends:
        try:
            pages = extractor(source)
            validate_extracted_pages(name, pages)
            result = PdfBackendResult(name=name, pages=pages)
        except Exception as exc:
            result = PdfBackendResult(name=name, pages=[], error=str(exc))
        attempted.append(result)

        if result.pages and (best is None or backend_sort_key(result) > backend_sort_key(best)):
            best = result
        if result.pages and not low_text_coverage(result):
            return result

    if best is not None:
        return best

    details = "; ".join(f"{result.name}: {result.error or 'no pages'}" for result in attempted)
    raise PdfExtractionError(
        "Missing dependency or no readable pages: install pypdf, pdfplumber, or pdftotext to extract PDF text."
        f" Attempts: {details}"
    )


def render_markdown(path: Path, result: PdfBackendResult, *, low_coverage: bool) -> str:
    out = [f"# Extracted PDF Text: {path.name}", ""]
    if low_coverage:
        out.extend([LOW_COVERAGE_WARNING, ""])
    out.extend(
        [
            f"- Backend: `{result.name}`",
            f"- Pages: {result.page_count}",
            f"- Empty text pages: {result.empty_page_count}",
            f"- Text characters: {result.text_char_count}",
            f"- Low coverage: {str(low_coverage).lower()}",
            "",
        ]
    )
    for idx, text in enumerate(result.pages, start=1):
        out.append(f"## Page {idx}")
        out.append("")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            out.extend(lines)
        else:
            out.append("[No extractable text]")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def extract_pdf_result(path: Path) -> PdfExtractionResult:
    path = validate_pdf_input(path)
    try:
        source = read_bytes_no_follow(path, max_bytes=MAX_PDF_INPUT_BYTES)
    except (OSError, ValueError) as exc:
        raise PdfExtractionError(f"{path}: {exc}") from exc
    result = choose_backend(source)
    is_low_coverage = low_text_coverage(result)
    return PdfExtractionResult(
        markdown=render_markdown(path, result, low_coverage=is_low_coverage),
        backend=result.name,
        low_coverage=is_low_coverage,
        empty_pages=result.empty_page_count,
        char_count=result.text_char_count,
        page_count=result.page_count,
    )


def extract_pdf(path: Path) -> str:
    return extract_pdf_result(path).markdown


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract PDF text into Markdown.")
    parser.add_argument("pdf", type=Path, help="Path to a .pdf file")
    parser.add_argument("--out", type=Path, help="Output Markdown path")
    args = parser.parse_args()

    try:
        md = extract_pdf(args.pdf)
    except PdfExtractionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.out:
        try:
            safe_write_text(args.out, md)
        except (OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    else:
        print(md, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
