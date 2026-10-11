#!/usr/bin/env python3
"""Generate small JPEG previews for a vision pass on already-rendered pages.

Why: a full-resolution 240-DPI page PNG is often 1-5 MB. Base64-encoded into a
model request that easily exceeds an API gateway's body buffer and fails with
HTTP 413 ``Payload Too Large: Failed to buffer the request body``. Sending a
downscaled JPEG instead (longest edge <= --max-edge) cuts the payload by roughly
an order of magnitude.

This is useful when a run was rendered before previews existed: point it at the
``rendered_pages`` directory and it writes ``page_NNNN.vision.jpg`` next to each
``page_NNNN.png`` without re-extracting or re-rendering. New runs already write
these previews automatically (see render_pages.py / batch_ocr.py).

Standard library + Pillow only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
DEFAULT_MAX_EDGE = 1568
DEFAULT_QUALITY = 80


def _iter_images(paths: list[Path], recursive: bool) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_file():
            if path.suffix.lower() in IMAGE_EXTENSIONS and not path.name.endswith(".vision.jpg"):
                found.append(path)
            continue
        pattern = "**/*" if recursive else "*"
        for candidate in sorted(path.glob(pattern)):
            if (
                candidate.is_file()
                and candidate.suffix.lower() in IMAGE_EXTENSIONS
                and not candidate.name.endswith(".vision.jpg")
            ):
                found.append(candidate)
    return found


def make_preview(source: Path, destination: Path, max_edge: int, quality: int) -> dict:
    from PIL import Image

    destination.parent.mkdir(parents=True, exist_ok=True)
    before = source.stat().st_size
    with Image.open(source) as image:
        image = image.convert("RGB")
        width, height = image.size
        longest = max(width, height)
        scale = min(1.0, float(max_edge) / longest) if longest else 1.0
        if scale < 1.0:
            image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
        image.save(destination, "JPEG", quality=int(quality), optimize=True)
    after = destination.stat().st_size
    return {
        "source": str(source),
        "preview": str(destination),
        "before_bytes": before,
        "after_bytes": after,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate small JPEG vision previews.")
    parser.add_argument("inputs", nargs="+", help="Image files or directories to convert")
    parser.add_argument("--out-dir", default=None, help="Write previews here (default: next to each source)")
    parser.add_argument("--max-edge", type=int, default=DEFAULT_MAX_EDGE, help="Longest edge in px")
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY, help="JPEG quality")
    parser.add_argument("--recursive", action="store_true", help="Recurse into directories")
    parser.add_argument("--json", action="store_true", help="Print a JSON report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inputs = [Path(item).expanduser().resolve() for item in args.inputs]
    for item in inputs:
        if not item.exists():
            raise SystemExit(f"input not found: {item}")
    images = _iter_images(inputs, args.recursive)
    if not images:
        raise SystemExit("no images found")

    out_dir = Path(args.out_dir).expanduser() if args.out_dir else None
    report: list[dict] = []
    total_before = 0
    total_after = 0
    for image in images:
        if out_dir is not None:
            destination = out_dir / f"{image.stem}.vision.jpg"
        else:
            destination = image.with_name(f"{image.stem}.vision.jpg")
        entry = make_preview(image, destination, args.max_edge, args.quality)
        report.append(entry)
        total_before += entry["before_bytes"]
        total_after += entry["after_bytes"]
        if not args.json:
            print(
                f"{image.name}: {entry['before_bytes']/1e6:.2f}MB -> "
                f"{entry['after_bytes']/1e6:.2f}MB  ({destination.name})"
            )

    if args.json:
        print(json.dumps({"count": len(report), "items": report}, ensure_ascii=False, indent=2))
    else:
        ratio = (total_after / total_before) if total_before else 0
        print(
            f"\n{len(report)} previews  total {total_before/1e6:.2f}MB -> "
            f"{total_after/1e6:.2f}MB  ({ratio*100:.0f}%)  max-edge={args.max_edge} quality={args.quality}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
