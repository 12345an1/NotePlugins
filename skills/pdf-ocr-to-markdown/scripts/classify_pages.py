#!/usr/bin/env python3
"""Deterministic page quality classifier for OCR/vision routing."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROUTES = ("text_only", "visual_focus", "verify_text_and_visual", "full_vision_ocr")
TEXT_QUALITIES = ("good", "medium", "bad")
VISUAL_DENSITIES = ("low", "medium", "high")
GARBAGE_CHARS = ("\ufffd", "\u25a1", "\u25a0", "\u25ab", "\u25ad", "\u2610")


def empty_route_counts() -> dict[str, int]:
    return {route: 0 for route in ROUTES}


def _text_stats(text: str) -> dict[str, Any]:
    raw_length = len(text)
    stripped = text.strip()
    char_count = len(stripped)
    non_space_count = sum(1 for char in stripped if not char.isspace())
    whitespace_ratio = 1.0 - (non_space_count / char_count) if char_count else 1.0
    garbage_count = sum(stripped.count(char) for char in GARBAGE_CHARS)
    garbage_count += len(re.findall(r"(?:cid:\d+|\\x[0-9a-fA-F]{2})", stripped))
    garbage_ratio = garbage_count / max(char_count, 1)
    lines = [line.rstrip() for line in stripped.splitlines() if line.strip()]
    single_char_lines = sum(1 for line in lines if len(line.strip()) <= 1)
    single_char_line_ratio = single_char_lines / max(len(lines), 1)
    repeated_tokens = 0
    tokens = re.findall(r"\S+", stripped)
    if tokens:
        counts = Counter(tokens)
        repeated_tokens = sum(1 for _, count in counts.items() if count >= 8)
    return {
        "raw_length": raw_length,
        "char_count": char_count,
        "non_space_count": non_space_count,
        "whitespace_ratio": round(whitespace_ratio, 4),
        "garbage_count": garbage_count,
        "garbage_ratio": round(garbage_ratio, 4),
        "line_count": len(lines),
        "single_char_line_ratio": round(single_char_line_ratio, 4),
        "repeated_token_count": repeated_tokens,
    }


def classify_text_quality(text: str, metadata: dict[str, Any] | None = None) -> tuple[str, list[str], list[str]]:
    metadata = metadata or {}
    stats = _text_stats(text)
    reasons: list[str] = []
    warnings: list[str] = []
    area = float(metadata.get("width") or 0) * float(metadata.get("height") or 0)

    if stats["char_count"] == 0:
        reasons.append("no embedded text extracted")
        return "bad", reasons, warnings
    if stats["char_count"] < 40:
        reasons.append("embedded text is very short")
        if area > 250000:
            warnings.append("text is suspiciously short relative to page dimensions")
        return "bad", reasons, warnings
    if stats["garbage_ratio"] > 0.03:
        reasons.append("high replacement/mojibake character ratio")
        return "bad", reasons, warnings
    if stats["single_char_line_ratio"] > 0.45:
        reasons.append("one-character-per-line pattern suggests broken extraction")
        return "bad", reasons, warnings

    medium = False
    if stats["char_count"] < 250:
        medium = True
        reasons.append("embedded text is short enough to require verification")
    if stats["garbage_ratio"] > 0.005:
        medium = True
        reasons.append("some replacement/mojibake characters are present")
    if stats["single_char_line_ratio"] > 0.25:
        medium = True
        reasons.append("line structure may be degraded")
    if stats["whitespace_ratio"] > 0.58 and stats["char_count"] > 100:
        medium = True
        reasons.append("excessive whitespace suggests table or ordering issues")
    if stats["repeated_token_count"] >= 3:
        medium = True
        reasons.append("repeated tokens suggest extraction noise")
    if area > 350000 and stats["char_count"] < 120:
        medium = True
        warnings.append("text is short relative to page dimensions")

    if medium:
        return "medium", reasons, warnings

    reasons.append("embedded text is long enough and has low garbage ratio")
    return "good", reasons, warnings


def classify_visual_density(
    metadata: dict[str, Any] | None = None,
    rendered_metadata: dict[str, Any] | None = None,
    is_image: bool = False,
) -> tuple[str, list[str], list[str]]:
    metadata = metadata or {}
    rendered_metadata = rendered_metadata or {}
    reasons: list[str] = []
    warnings: list[str] = []

    if is_image:
        reasons.append("image input requires visual analysis")
        return "high", reasons, warnings

    images = int(metadata.get("images") or 0)
    lines = int(metadata.get("lines") or 0)
    rectangles = int(metadata.get("rectangles") or 0)
    curves = int(metadata.get("curves") or 0)
    vector_objects = lines + rectangles + curves

    if images > 0:
        reasons.append(f"{images} image object(s) detected")
    if lines >= 10:
        reasons.append(f"{lines} line object(s) suggest tables, charts, or diagrams")
    if rectangles >= 6:
        reasons.append(f"{rectangles} rectangle object(s) suggest boxes, tables, or callouts")
    if curves >= 8:
        reasons.append(f"{curves} curve object(s) suggest diagrams or charts")

    if images >= 1 or vector_objects >= 20 or lines >= 18 or rectangles >= 12:
        return "high", reasons or ["dense visual/vector structure detected"], warnings
    if vector_objects >= 5:
        return "medium", reasons or ["some vector structure detected"], warnings

    if rendered_metadata and not metadata:
        warnings.append("visual density is limited because PDF object metadata was unavailable")

    return "low", reasons or ["few visual objects detected"], warnings


def classify_page(
    page: int,
    text: str,
    metadata: dict[str, Any] | None = None,
    rendered_metadata: dict[str, Any] | None = None,
    force_full_vision: bool = False,
    is_image: bool = False,
) -> dict[str, Any]:
    text_quality, text_reasons, text_warnings = classify_text_quality(text, metadata)
    visual_density, visual_reasons, visual_warnings = classify_visual_density(
        metadata=metadata,
        rendered_metadata=rendered_metadata,
        is_image=is_image,
    )

    if force_full_vision:
        route = "full_vision_ocr"
        route_reason = "force-full-vision enabled"
    elif text_quality == "bad":
        route = "full_vision_ocr"
        route_reason = "bad text quality"
    elif text_quality == "medium":
        route = "verify_text_and_visual"
        route_reason = "medium text quality"
    elif visual_density in {"medium", "high"}:
        route = "visual_focus"
        route_reason = "good text with meaningful visual density"
    else:
        route = "text_only"
        route_reason = "good text with low visual density"

    return {
        "page": page,
        "text_quality": text_quality,
        "visual_density": visual_density,
        "route": route,
        "reasons": text_reasons + visual_reasons + [route_reason],
        "warnings": text_warnings + visual_warnings,
        "signals": {
            "text": _text_stats(text),
            "pdf_objects": metadata or {},
            "rendered": rendered_metadata or {},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify a page into an OCR/vision route.")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--metadata-json", default=None)
    parser.add_argument("--rendered-json", default=None)
    parser.add_argument("--force-full-vision", action="store_true")
    parser.add_argument("--is-image", action="store_true")
    args = parser.parse_args()

    text = Path(args.text_file).read_text(encoding="utf-8")
    metadata = {}
    rendered = {}
    if args.metadata_json:
        metadata = json.loads(Path(args.metadata_json).read_text(encoding="utf-8"))
    if args.rendered_json:
        rendered = json.loads(Path(args.rendered_json).read_text(encoding="utf-8"))

    result = classify_page(
        page=args.page,
        text=text,
        metadata=metadata,
        rendered_metadata=rendered,
        force_full_vision=args.force_full_vision,
        is_image=args.is_image,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
