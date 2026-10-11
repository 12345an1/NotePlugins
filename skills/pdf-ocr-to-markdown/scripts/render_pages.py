#!/usr/bin/env python3
"""Render PDF pages to stable PNG filenames and stage image inputs."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# Vision-pass preview settings. A full-resolution 240-DPI page PNG can be
# several MB; base64-encoded that easily exceeds an API gateway's request-body
# buffer and trips HTTP 413 "Payload Too Large: Failed to buffer the request
# body". The vision pass should use a downscaled JPEG preview instead.
DEFAULT_VISION_MAX_EDGE = 1568
DEFAULT_VISION_JPEG_QUALITY = 80


class RenderIssue(RuntimeError):
    """Raised when local rendering is not available or fails."""


def is_image_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def dependency_status() -> dict[str, Any]:
    status: dict[str, Any] = {
        "pymupdf": False,
        "pillow": False,
        "usable_pdf_renderer": False,
        "install": "python -m pip install pymupdf Pillow",
    }
    try:
        import fitz  # noqa: F401

        status["pymupdf"] = True
    except Exception as exc:  # pragma: no cover - environment-specific
        status["pymupdf_error"] = str(exc)

    try:
        from PIL import Image  # noqa: F401

        status["pillow"] = True
    except Exception as exc:  # pragma: no cover - environment-specific
        status["pillow_error"] = str(exc)

    status["usable_pdf_renderer"] = bool(status["pymupdf"])
    return status


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def make_vision_preview(
    source: str | Path,
    destination: str | Path,
    max_edge: int = DEFAULT_VISION_MAX_EDGE,
    quality: int = DEFAULT_VISION_JPEG_QUALITY,
) -> dict[str, Any]:
    """Downscale an image to a small JPEG suitable for one vision-model request.

    Keeps the longest edge at or below ``max_edge`` and re-encodes as JPEG.
    For a typical full-page scan this cuts the payload by roughly an order of
    magnitude (for example ~4 MB PNG -> ~0.3-0.5 MB JPEG).
    """

    from PIL import Image

    src = Path(source)
    dst = Path(destination)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as image:
        image = image.convert("RGB")
        width, height = image.size
        longest = max(width, height)
        scale = min(1.0, float(max_edge) / longest) if longest else 1.0
        if scale < 1.0:
            image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
        image.save(dst, "JPEG", quality=int(quality), optimize=True)
    preview_width, preview_height = _image_dimensions(dst)
    return {
        "vision_image": str(dst),
        "vision_width_px": preview_width,
        "vision_height_px": preview_height,
        "vision_bytes": dst.stat().st_size,
    }


def render_pdf_pages(
    pdf_path: str | Path,
    output_dir: str | Path,
    dpi: int = 240,
    vision_max_edge: int = DEFAULT_VISION_MAX_EDGE,
    vision_quality: int = DEFAULT_VISION_JPEG_QUALITY,
) -> list[dict[str, Any]]:
    """Render every PDF page as page_0001.png, page_0002.png, ..."""

    pdf = Path(pdf_path)
    out = Path(output_dir)
    if not pdf.exists():
        raise FileNotFoundError(str(pdf))

    status = dependency_status()
    if not status["pymupdf"]:
        raise RenderIssue(
            "PyMuPDF is required for PDF rendering. Install with: "
            f"{status['install']}"
        )

    import fitz

    out.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    document = fitz.open(str(pdf))
    try:
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = out / f"page_{index + 1:04d}.png"
            pixmap.save(str(image_path))
            entry: dict[str, Any] = {
                "page": index + 1,
                "image_path": str(image_path),
                "width_px": pixmap.width,
                "height_px": pixmap.height,
                "dpi": dpi,
                "png_bytes": image_path.stat().st_size,
            }
            try:
                entry.update(
                    make_vision_preview(
                        image_path,
                        out / f"page_{index + 1:04d}.vision.jpg",
                        max_edge=vision_max_edge,
                        quality=vision_quality,
                    )
                )
            except Exception as exc:  # preview is best-effort; never fail the render
                entry["vision_error"] = str(exc)
            rendered.append(entry)
    finally:
        document.close()
    return rendered


def stage_image_file(
    image_path: str | Path,
    output_dir: str | Path,
    vision_max_edge: int = DEFAULT_VISION_MAX_EDGE,
    vision_quality: int = DEFAULT_VISION_JPEG_QUALITY,
) -> list[dict[str, Any]]:
    """Copy an image input into the rendered-pages area as a one-page document."""

    source = Path(image_path)
    out = Path(output_dir)
    if not source.exists():
        raise FileNotFoundError(str(source))
    if not is_image_file(source):
        raise RenderIssue(f"Unsupported image extension: {source.suffix}")

    out.mkdir(parents=True, exist_ok=True)
    staged = out / f"page_0001{source.suffix.lower()}"
    shutil.copy2(source, staged)
    width, height = _image_dimensions(staged)
    entry: dict[str, Any] = {
        "page": 1,
        "image_path": str(staged),
        "width_px": width,
        "height_px": height,
        "dpi": None,
        "source_image": str(source),
        "png_bytes": staged.stat().st_size,
    }
    try:
        entry.update(
            make_vision_preview(
                staged,
                out / "page_0001.vision.jpg",
                max_edge=vision_max_edge,
                quality=vision_quality,
            )
        )
    except Exception as exc:
        entry["vision_error"] = str(exc)
    return [entry]


def main() -> int:
    parser = argparse.ArgumentParser(description="Render PDF pages or stage an image input.")
    parser.add_argument("input", help="PDF or image file")
    parser.add_argument("--out-dir", required=True, help="Directory for rendered page images")
    parser.add_argument("--dpi", type=int, default=240, help="Render DPI for PDFs")
    parser.add_argument(
        "--vision-max-edge",
        type=int,
        default=DEFAULT_VISION_MAX_EDGE,
        help="Longest edge (px) of the small JPEG preview used for the vision pass",
    )
    parser.add_argument(
        "--vision-quality",
        type=int,
        default=DEFAULT_VISION_JPEG_QUALITY,
        help="JPEG quality of the vision preview",
    )
    args = parser.parse_args()

    source = Path(args.input)
    try:
        if source.suffix.lower() == ".pdf":
            result = render_pdf_pages(
                source, args.out_dir, args.dpi, args.vision_max_edge, args.vision_quality
            )
        elif is_image_file(source):
            result = stage_image_file(
                source, args.out_dir, args.vision_max_edge, args.vision_quality
            )
        else:
            raise RenderIssue(f"Unsupported file type: {source.suffix}")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"ok": True, "pages": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
