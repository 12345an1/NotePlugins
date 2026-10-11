#!/usr/bin/env python3
"""Merge page-level OCR artifacts into file-level and batch-level outputs."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any

PAGE_JSON_KEYS = {
    "source_file",
    "page",
    "route",
    "text_quality",
    "visual_density",
    "content_blocks",
    "text_markdown",
    "visual_annotations",
    "tables",
    "figures",
    "emphasis",
    "warnings",
    "uncertainties",
}
TRUSTED_RAW_IDENTITY_KEYS = {"source_file", "page", "route"}
TRUSTED_RAW_STATE_KEYS = TRUSTED_RAW_IDENTITY_KEYS | {"text_quality", "visual_density"}
SIDECAR_SUFFIXES = {
    "markdown": ".md",
}


def _safe_sidecar_component(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_-")
    return safe or "source"


def _sidecar_stem(source_path: Path) -> str:
    return _safe_sidecar_component(source_path.stem or source_path.name or "source")


def _sidecar_label_and_extension(suffix: str) -> tuple[str, str]:
    suffix_parts = [part for part in suffix.lstrip(".").split(".") if part]
    if not suffix_parts:
        suffix_parts = ["txt"]
    extension = _safe_sidecar_component(suffix_parts[-1])
    label_parts = ["ocr", *suffix_parts[:-1]]
    label = "_".join(_safe_sidecar_component(part) for part in label_parts)
    return label, extension


def _sidecar_path_for_stem(source_path: Path, safe_stem: str, suffix: str) -> Path:
    label, extension = _sidecar_label_and_extension(suffix)
    return source_path.with_name(f"{safe_stem}_{label}.{extension}")


def sidecar_path(source_path: Path, suffix: str, run_id: str) -> Path:
    return _sidecar_path_for_stem(source_path, _sidecar_stem(source_path), suffix)


def sidecar_paths(
    source_path: Path,
    run_id: str,
    used_paths: set[Path] | None = None,
) -> dict[str, Path]:
    base_stem = _sidecar_stem(source_path)
    counter = 1
    while True:
        safe_stem = base_stem if counter == 1 else f"{base_stem}_{counter}"
        candidates = {
            key: _sidecar_path_for_stem(source_path, safe_stem, suffix)
            for key, suffix in SIDECAR_SUFFIXES.items()
        }
        if used_paths is None or not any(path in used_paths for path in candidates.values()):
            if used_paths is not None:
                used_paths.update(candidates.values())
            return candidates
        counter += 1


def _sidecar_path(source_path: Path, suffix: str, run_id: str) -> Path:
    return sidecar_path(source_path, suffix, run_id)


def _planned_sidecar_paths(manifest: dict[str, Any]) -> dict[str, Path] | None:
    planned = manifest.get("planned_sidecars")
    if not isinstance(planned, dict):
        return None
    sidecars: dict[str, Path] = {}
    for key in SIDECAR_SUFFIXES:
        value = planned.get(key)
        if not value:
            return None
        sidecars[key] = Path(value)
    return sidecars

def write_sidecar_outputs(manifest: dict[str, Any], output: dict[str, Any]) -> dict[str, str]:
    source_path = Path(manifest.get("source_path", ""))
    run_id = str(manifest.get("run_id") or "run")
    if not source_path:
        return {}
    sidecars = _planned_sidecar_paths(manifest) or sidecar_paths(source_path, run_id)
    shutil.copy2(output["final_md"], sidecars["markdown"])
    return {"markdown": str(sidecars["markdown"])}


def read_json(path: str | Path, default: Any = None) -> Any:
    file_path = Path(path)
    if not file_path.exists():
        return default
    return json.loads(file_path.read_text(encoding="utf-8"))


def write_json(path: str | Path, data: Any) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _same_identity_value(key: str, expected: Any, actual: Any) -> bool:
    if key == "page":
        try:
            return int(expected) == int(actual)
        except (TypeError, ValueError):
            return False
    return str(expected) == str(actual)


def _validate_content_blocks(value: Any) -> None:
    if not isinstance(value, list):
        raise ValueError("raw visual output content_blocks must be a list")
    for index, block in enumerate(value):
        if isinstance(block, str):
            continue
        if not isinstance(block, dict):
            raise ValueError(f"raw visual output content_blocks[{index}] must be an object or string")
        block_type = block.get("type")
        if block_type is not None and not isinstance(block_type, str):
            raise ValueError(f"raw visual output content_blocks[{index}].type must be a string")
        for key in ("text", "markdown", "content", "summary", "label", "title"):
            if key in block and block[key] is not None and not isinstance(block[key], str):
                raise ValueError(f"raw visual output content_blocks[{index}].{key} must be a string")


def merge_raw_visual_json(raw: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    for key in TRUSTED_RAW_IDENTITY_KEYS:
        if key in raw and not _same_identity_value(key, existing.get(key), raw[key]):
            raise ValueError(f"raw visual output {key} does not match the planned page")

    page_json = dict(existing)
    for key in PAGE_JSON_KEYS - TRUSTED_RAW_STATE_KEYS:
        if key not in raw:
            continue
        if key == "content_blocks":
            _validate_content_blocks(raw[key])
        page_json[key] = raw[key]
    return page_json


def _as_markdown_list(items: list[Any], empty: str = "Not applicable.") -> str:
    if not items:
        return empty
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            summary = item.get("summary") or item.get("text") or json.dumps(item, ensure_ascii=False)
            lines.append(f"- {summary}")
        else:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _normalize_markdown_fragment(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _block_body(block: dict[str, Any]) -> str:
    for key in ("markdown", "text", "content", "summary"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_markdown_fragment(value)
    return ""


FIGURE_BLOCK_TYPES = {"figure", "diagram", "chart", "image"}
FIGURE_MODES = ("crop", "page", "placeholder")


class FigureContext:
    """Embed figures as real images by cropping the rendered page image.

    Resolution order for a figure/table/image block:
      1. ``bbox`` present -> crop that region (the precise figure).
      2. no ``bbox``      -> trim page margins to the ink bounds (when ``trim`` is
                             on), so the fallback is not a full-size page image.
    The result is capped to ``max_edge`` and written next to the note as an asset;
    the block renders as a Markdown image instead of a ``[Figure: ...]`` placeholder.
    """

    def __init__(
        self,
        page_image: str | Path | None,
        assets_dir: str | Path | None,
        rel_prefix: str,
        mode: str = "crop",
        padding: float = 0.01,
        page_label: str = "0000",
        trim: bool = True,
        max_edge: int = 1600,
    ) -> None:
        self.page_image = Path(page_image) if page_image else None
        self.assets_dir = Path(assets_dir) if assets_dir else None
        self.rel_prefix = (rel_prefix or "").rstrip("/")
        self.mode = mode if mode in FIGURE_MODES else "crop"
        self.padding = max(0.0, float(padding))
        self.page_label = page_label
        self.trim = bool(trim)
        self.max_edge = max(0, int(max_edge or 0))
        self._counter = 0

    def _bbox(self, block: dict[str, Any]) -> tuple[float, float, float, float] | None:
        raw = block.get("bbox") or block.get("bbox_norm")
        if isinstance(raw, (list, tuple)) and len(raw) == 4:
            try:
                values = [float(v) for v in raw]
            except (TypeError, ValueError):
                return None
        else:
            px = block.get("bbox_px")
            if not (isinstance(px, (list, tuple)) and len(px) == 4 and self.page_image):
                return None
            try:
                x0, y0, x1, y1 = (float(v) for v in px)
            except (TypeError, ValueError):
                return None
            width, height = _image_size(self.page_image)
            if not width or not height:
                return None
            values = [x0 / width, y0 / height, x1 / width, y1 / height]
        x0, y0, x1, y1 = values
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        clamp = lambda v: min(max(v, 0.0), 1.0)
        return clamp(x0), clamp(y0), clamp(x1), clamp(y1)

    def _finish(self, image: Any, stem: str) -> str:
        if self.max_edge:
            width, height = image.size
            longest = max(width, height)
            if longest > self.max_edge:
                scale = self.max_edge / longest
                image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
        self._counter += 1
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        if stem == "fig":
            # Precise bbox crops stay lossless PNG (usually small).
            name = f"fig_p{self.page_label}_{self._counter:02d}.png"
            image.save(self.assets_dir / name, "PNG", optimize=True)
        else:
            # Page fallback: JPEG keeps bytes small even for scans.
            name = f"{stem}_p{self.page_label}_{self._counter:02d}.jpg"
            image.save(self.assets_dir / name, "JPEG", quality=85, optimize=True)
        return name

    def render(self, block: dict[str, Any], label: str) -> str | None:
        if self.mode == "placeholder":
            return None
        if self.page_image is None or not self.page_image.exists() or self.assets_dir is None:
            return None
        try:
            from PIL import Image
        except Exception:
            return None
        try:
            with Image.open(self.page_image) as opened:
                image = opened.convert("RGB")
                width, height = image.size
                bbox = self._bbox(block) if self.mode == "crop" else None
                if bbox is not None:
                    x0, y0, x1, y1 = bbox
                    left = max(0, int((x0 - self.padding) * width))
                    top = max(0, int((y0 - self.padding) * height))
                    right = min(width, int((x1 + self.padding) * width))
                    bottom = min(height, int((y1 + self.padding) * height))
                    if right - left < 8 or bottom - top < 8:
                        left, top, right, bottom = 0, 0, width, height
                        stem = "page"
                    else:
                        stem = "fig"
                    image = image.crop((left, top, right, bottom))
                else:
                    stem = "page"
                    if self.trim:
                        bounds = _ink_bounds(image)
                        if bounds is not None:
                            image = image.crop(bounds)
                name = self._finish(image, stem)
        except Exception:
            return None
        alt = label or "figure"
        prefix = f"{self.rel_prefix}/" if self.rel_prefix else ""
        return f"![{alt}]({prefix}{name})"


def _ink_bounds(
    image: Any,
    ink_threshold: int = 245,
    min_row_fraction: float = 0.002,
    margin_ratio: float = 0.01,
) -> tuple[int, int, int, int] | None:
    """Best-effort content bounds: drop near-white margins around the ink.

    Uses cheap row/column ink-density projections (PIL only). Returns ``None``
    when there is no clear margin to trim (blank page, full-bleed art, dark
    scan), so callers keep the uncropped image.
    """

    try:
        from PIL import Image
    except Exception:
        return None
    try:
        gray = image.convert("L")
        mask = gray.point(lambda p: 255 if p < ink_threshold else 0, "L")
        width, height = mask.size
        if width < 8 or height < 8:
            return None
        rows = list(mask.resize((1, height), Image.BOX).getdata())
        cols = list(mask.resize((width, 1), Image.BOX).getdata())
    except Exception:
        return None

    def span(values: list[int]) -> tuple[int, int] | None:
        hits = [i for i, v in enumerate(values) if v / 255.0 >= min_row_fraction]
        return (hits[0], hits[-1]) if hits else None

    vertical = span(rows)
    horizontal = span(cols)
    if vertical is None or horizontal is None:
        return None
    y0, y1 = vertical
    x0, x1 = horizontal
    if (x0, y0, x1, y1) == (0, 0, width - 1, height - 1):
        return None
    margin_x = int((x1 - x0 + 1) * margin_ratio)
    margin_y = int((y1 - y0 + 1) * margin_ratio)
    x0 = max(0, x0 - margin_x)
    y0 = max(0, y0 - margin_y)
    x1 = min(width - 1, x1 + margin_x)
    y1 = min(height - 1, y1 + margin_y)
    return (x0, y0, x1 + 1, y1 + 1)


def _image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def _render_content_block(block: Any, figures: FigureContext | None = None) -> str:
    if isinstance(block, str):
        return _normalize_markdown_fragment(block)
    if not isinstance(block, dict):
        return _normalize_markdown_fragment(json.dumps(block, ensure_ascii=False))

    block_type = str(block.get("type") or "paragraph").strip().lower()
    body = _block_body(block)
    label = str(block.get("label") or block.get("title") or "").strip()

    if block_type == "heading":
        if not body:
            return ""
        try:
            level = int(block.get("level") or 2)
        except (TypeError, ValueError):
            level = 2
        level = min(max(level, 2), 6)
        return f"{'#' * level} {body}"

    if block_type in FIGURE_BLOCK_TYPES:
        image_md = figures.render(block, label) if figures is not None else None
        if image_md:
            return f"{image_md}\n{body}".rstrip() if body else image_md
        heading = f"[Figure: {label}]" if label else "[Figure]"
        return f"{heading}\n{body}".rstrip() if body else heading

    if block_type == "table":
        return body or "[Table: unclear]"

    if block_type == "equation":
        return body or "[Equation: unclear]"

    if block_type == "unclear_block":
        return body or "[unclear]"

    return body


def _render_content_blocks(blocks: list[Any], figures: FigureContext | None = None) -> str:
    if not isinstance(blocks, list):
        blocks = [blocks]
    rendered = [_render_content_block(block, figures) for block in blocks]
    rendered = [block for block in rendered if block]
    return "\n\n".join(rendered)


def normalize_text_to_markdown(text: str) -> str:
    """Normalize extracted text into conservative Markdown without adding claims."""

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    normalized_lines: list[str] = []
    blank_pending = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            blank_pending = True
            continue
        if blank_pending and normalized_lines:
            normalized_lines.append("")
        blank_pending = False
        normalized_lines.append(line)
    return "\n".join(normalized_lines)


def page_json_template(
    source_file: str,
    page: int,
    route: str,
    text_quality: str,
    visual_density: str,
    text_markdown: str = "",
    warnings: list[str] | None = None,
    uncertainties: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source_file": source_file,
        "page": page,
        "route": route,
        "text_quality": text_quality,
        "visual_density": visual_density,
        "content_blocks": [],
        "text_markdown": text_markdown,
        "visual_annotations": [],
        "tables": [],
        "figures": [],
        "emphasis": [],
        "warnings": warnings or [],
        "uncertainties": uncertainties or [],
    }


def build_page_markdown(page_json: dict[str, Any], figures: FigureContext | None = None) -> str:
    page = int(page_json.get("page") or 0)
    page_label = f"{page:04d}" if page else "0000"
    text = page_json.get("text_markdown") or ""
    content_blocks = page_json.get("content_blocks") or []

    body = _render_content_blocks(content_blocks, figures)
    if not body:
        body = text or "[unclear]"

    sections = [
        f"<!-- page: {page_label} -->",
        "",
        body,
    ]
    return "\n".join(sections).rstrip() + "\n"


def write_page_artifacts(
    pages_dir: str | Path,
    page_json: dict[str, Any],
    figures: FigureContext | None = None,
) -> tuple[Path, Path]:
    pages_path = Path(pages_dir)
    pages_path.mkdir(parents=True, exist_ok=True)
    page = int(page_json["page"])
    md_path = pages_path / f"page_{page:04d}.md"
    json_path = pages_path / f"page_{page:04d}.json"
    md_path.write_text(build_page_markdown(page_json, figures), encoding="utf-8")
    write_json(json_path, page_json)
    return md_path, json_path


def write_quality_report(path: str | Path, route_report: list[dict[str, Any]]) -> None:
    lines = [
        "# Quality Report",
        "",
        "| Page | Text quality | Visual density | Route | Reasons | Warnings |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for page in route_report:
        reasons = "; ".join(page.get("reasons") or [])
        warnings = "; ".join(page.get("warnings") or [])
        lines.append(
            "| {page} | {text_quality} | {visual_density} | {route} | {reasons} | {warnings} |".format(
                page=page.get("page"),
                text_quality=page.get("text_quality"),
                visual_density=page.get("visual_density"),
                route=page.get("route"),
                reasons=reasons.replace("|", "\\|"),
                warnings=warnings.replace("|", "\\|"),
            )
        )
    Path(path).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def merge_file_outputs(
    file_out_dir: str | Path,
    manifest: dict[str, Any],
    route_report: list[dict[str, Any]],
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    file_dir = Path(file_out_dir)
    pages_dir = file_dir / "pages"
    metadata_dir = file_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    pages = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(pages_dir.glob("page_*.json"))
    ]
    final_md_parts = [
        f"# {manifest.get('source_file', file_dir.name)}",
        "",
        f"- Source file: `{manifest.get('source_file', file_dir.name)}`",
        f"- Pages: {len(pages)}",
        f"- Status: {manifest.get('status', 'processed')}",
        "",
    ]
    for page_json in pages:
        page_md_path = pages_dir / f"page_{int(page_json['page']):04d}.md"
        if page_md_path.exists():
            final_md_parts.append(page_md_path.read_text(encoding="utf-8").rstrip())
            final_md_parts.append("")

    final_json = {
        "metadata": manifest,
        "pages": pages,
        "errors": errors or [],
    }

    final_md_path = file_dir / "final.md"
    final_json_path = file_dir / "final.json"
    quality_path = file_dir / "quality_report.md"

    final_md_path.write_text("\n".join(final_md_parts).rstrip() + "\n", encoding="utf-8")
    write_json(final_json_path, final_json)
    write_quality_report(quality_path, route_report)
    write_json(metadata_dir / "manifest.json", manifest)
    write_json(metadata_dir / "route_report.json", route_report)
    write_json(metadata_dir / "errors.json", errors or [])

    return {
        "file_output_dir": str(file_dir),
        "final_md": str(final_md_path),
        "final_json": str(final_json_path),
        "quality_report": str(quality_path),
        "pages": len(pages),
    }


def write_batch_summary(out_run_dir: str | Path, summary: dict[str, Any]) -> None:
    out = Path(out_run_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "batch_summary.json", summary)

    routes = summary.get("routes", {})
    progress = summary.get("progress", [])
    lines = [
        "# Batch OCR Summary",
        "",
        f"- Run ID: `{summary.get('run_id', '')}`",
        f"- Total input files: {len(summary.get('input_files', []))}",
        f"- Processed files: {len(summary.get('processed_files', []))}",
        f"- Failed files: {len(summary.get('failed_files', []))}",
        f"- Total pages: {summary.get('total_pages', 0)}",
        f"- Completion status: `{summary.get('completion_status', 'unknown')}`",
        "",
        "## Progress",
        "",
    ]
    lines.extend(progress or ["No progress metrics recorded."])
    lines.extend(
        [
            "",
            "## Routes",
            "",
            f"- text_only: {routes.get('text_only', 0)}",
            f"- visual_focus: {routes.get('visual_focus', 0)}",
            f"- verify_text_and_visual: {routes.get('verify_text_and_visual', 0)}",
            f"- full_vision_ocr: {routes.get('full_vision_ocr', 0)}",
            "",
            "## Outputs",
            "",
        ]
    )
    outputs = summary.get("outputs", [])
    lines.extend([f"- `{output}`" for output in outputs] or ["None."])
    sidecars = summary.get("sidecar_outputs", [])
    lines.extend(["", "## Sidecar Outputs", ""])
    if sidecars:
        for sidecar in sidecars:
            lines.append(f"- Source: `{sidecar.get('source_file', '')}`")
            for key in ("markdown",):
                if sidecar.get(key):
                    lines.append(f"  - {key}: `{sidecar[key]}`")
    else:
        lines.append("None.")
    lines.extend(["", "## Failed Files", ""])
    lines.extend([f"- `{file}`" for file in summary.get("failed_files", [])] or ["None."])
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {warning}" for warning in summary.get("warnings", [])] or ["None."])
    lines.extend(["", "## Errors", ""])
    errors = summary.get("errors", [])
    lines.extend([f"- {error.get('file', '')}: {error.get('error', error)}" for error in errors] or ["None."])
    (out / "batch_summary.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_planned_manifest(out_run_dir: str | Path, manifest: dict[str, Any]) -> None:
    write_json(Path(out_run_dir) / "planned_manifest.json", manifest)


def build_intended_tree(run_id: str, file_slugs: list[str]) -> str:
    lines = [
        f".ocr_work/ocr_output/{run_id}/",
        "  batch_summary.md",
        "  batch_summary.json",
        "  planned_manifest.json",
        "  files/",
    ]
    for slug in file_slugs:
        lines.extend(
            [
                f"    {slug}/",
                "      final.md",
                "      final.json",
                "      quality_report.md",
                "      pages/",
                "        page_0001.md",
                "        page_0001.json",
                "      metadata/",
                "        manifest.json",
                "        route_report.json",
                "        errors.json",
                "        prompt_queue.json",
                "        visual_pass_instructions.md",
            ]
        )
    return "\n".join(lines)


def _raw_to_page_json(raw_path: Path, existing: dict[str, Any]) -> dict[str, Any]:
    page_json = dict(existing)
    if raw_path.suffix.lower() == ".json":
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        page_json = merge_raw_visual_json(raw, existing)
    else:
        raw_text = raw_path.read_text(encoding="utf-8").strip()
        page_json["text_markdown"] = raw_text
    page_json["raw_visual_output"] = str(raw_path)
    page_json["warnings"] = [
        warning
        for warning in page_json.get("warnings", [])
        if "Visual pass output is pending" not in warning
        and "Vision OCR output is pending" not in warning
    ]
    return page_json


def _find_page_image(work_run: Path, file_slug: str, page: int) -> Path | None:
    base = work_run / "rendered_pages" / file_slug
    for candidate in (base / f"page_{page:04d}.png", base / f"page_{page:04d}.vision.jpg"):
        if candidate.exists():
            return candidate
    return None


def refresh_from_raw_outputs(
    run_out_dir: str | Path,
    work_run_dir: str | Path,
    figure_mode: str = "crop",
    figure_padding: float = 0.01,
    figure_trim: bool = True,
    figure_max_edge: int = 1600,
) -> dict[str, Any]:
    """Update page artifacts from raw visual outputs and re-run file merges.

    Figures that carry a normalized ``bbox`` are cropped from the rendered page
    image and written as assets next to the Markdown sidecar, then embedded as
    relative image links. ``figure_mode`` is ``crop`` (crop the bbox, or the whole
    page when no bbox is present), ``page`` (always embed the whole page), or
    ``placeholder`` (keep the ``[Figure: ...]`` text placeholder).
    """

    run_out = Path(run_out_dir)
    work_run = Path(work_run_dir)
    files_root = run_out / "files"
    updated_pages = 0
    updated_files = 0
    sidecar_outputs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for file_dir in sorted(files_root.iterdir()) if files_root.exists() else []:
        if not file_dir.is_dir():
            continue
        metadata_dir = file_dir / "metadata"
        prompt_queue = read_json(metadata_dir / "prompt_queue.json", default=[])
        if not prompt_queue:
            continue

        manifest = read_json(metadata_dir / "manifest.json", default={})
        source_path = Path(manifest["source_path"]) if manifest.get("source_path") else None
        run_id = str(manifest.get("run_id") or "run")
        sidecar = None
        if source_path is not None:
            sidecar = (_planned_sidecar_paths(manifest) or sidecar_paths(source_path, run_id)).get("markdown")
        assets_dir = sidecar.parent / f"{sidecar.stem}_assets" if sidecar else None
        rel_prefix = f"./{sidecar.stem}_assets" if sidecar else ""

        changed = False
        for entry in prompt_queue:
            page = int(entry["page"])
            page_json_path = file_dir / "pages" / f"page_{page:04d}.json"
            existing = read_json(page_json_path, default=None)
            if existing is None:
                continue
            raw_dir = work_run / "raw_agent_outputs" / entry["file_slug"]
            candidates = [raw_dir / f"page_{page:04d}.json", raw_dir / f"page_{page:04d}.md"]
            raw_path = next((candidate for candidate in candidates if candidate.exists()), None)
            if raw_path is None:
                continue
            try:
                page_json = _raw_to_page_json(raw_path, existing)
                figures = None
                if assets_dir is not None:
                    figures = FigureContext(
                        _find_page_image(work_run, entry["file_slug"], page),
                        assets_dir,
                        rel_prefix,
                        mode=figure_mode,
                        padding=figure_padding,
                        page_label=f"{page:04d}",
                        trim=figure_trim,
                        max_edge=figure_max_edge,
                    )
                write_page_artifacts(file_dir / "pages", page_json, figures)
                updated_pages += 1
                changed = True
            except Exception as exc:
                errors.append({"file": file_dir.name, "page": page, "error": str(exc)})

        if changed:
            route_report = read_json(metadata_dir / "route_report.json", default=[])
            file_errors = read_json(metadata_dir / "errors.json", default=[])
            output = merge_file_outputs(file_dir, manifest, route_report, file_errors)
            sidecars = write_sidecar_outputs(manifest, output)
            if sidecars:
                sidecar_outputs.append({"source_file": manifest.get("source_path", ""), **sidecars})
            updated_files += 1

    completion_status = update_batch_completion_summary(run_out, sidecar_outputs, errors)
    return {
        "updated_files": updated_files,
        "updated_pages": updated_pages,
        "completion_status": completion_status,
        "sidecar_outputs": sidecar_outputs,
        "errors": errors,
    }


def _page_has_pending_visual(page_json: dict[str, Any]) -> bool:
    warnings = " ".join(str(item) for item in page_json.get("warnings", []))
    uncertainties = " ".join(str(item) for item in page_json.get("uncertainties", []))
    return "output is pending" in warnings or "has not been OCRed" in uncertainties


def update_batch_completion_summary(
    run_out: Path,
    sidecar_outputs: list[dict[str, Any]],
    merge_errors: list[dict[str, Any]],
) -> str:
    summary_path = run_out / "batch_summary.json"
    summary = read_json(summary_path, default=None)
    if summary is None:
        return "unknown"

    pending = False
    files_root = run_out / "files"
    for page_path in sorted(files_root.glob("*/pages/page_*.json")) if files_root.exists() else []:
        page_json = read_json(page_path, default={})
        if _page_has_pending_visual(page_json):
            pending = True
            break

    if summary.get("errors") or merge_errors:
        status = "failed"
    elif pending:
        status = "text_extraction_complete_visual_pending"
    else:
        status = "complete"

    summary["completion_status"] = status
    if sidecar_outputs:
        summary["sidecar_outputs"] = sidecar_outputs
        existing_outputs = list(summary.get("outputs", []))
        for sidecar in sidecar_outputs:
            for key in ("markdown",):
                if sidecar.get(key) and sidecar[key] not in existing_outputs:
                    existing_outputs.append(sidecar[key])
        summary["outputs"] = existing_outputs
    write_batch_summary(run_out, summary)
    return status


def cleanup_workdir(work_run_dir: str | Path) -> dict[str, Any]:
    work = Path(work_run_dir)
    removed_files = sum(1 for path in work.rglob("*") if path.is_file()) if work.exists() else 0
    if work.exists():
        shutil.rmtree(work)
    return {"work_dir": str(work), "removed_files": removed_files}


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge page outputs or refresh from raw visual outputs.")
    parser.add_argument("--run-out-dir", required=True, help="Existing output run directory")
    parser.add_argument("--work-run-dir", default=None, help="Work run directory containing raw_agent_outputs")
    parser.add_argument("--cleanup-work", action="store_true", help="Remove work-run-dir after merging raw outputs")
    parser.add_argument(
        "--figures",
        choices=FIGURE_MODES,
        default="crop",
        help="Figure rendering: crop the bbox from the page image (default), embed the whole page, or keep the text placeholder",
    )
    parser.add_argument(
        "--figure-padding",
        type=float,
        default=0.01,
        help="Extra padding (fraction of page size) around a cropped figure",
    )
    parser.add_argument(
        "--figure-trim",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When a figure has no bbox, trim page margins to the ink bounds (default: on)",
    )
    parser.add_argument(
        "--figure-max-edge",
        type=int,
        default=1600,
        help="Downscale an embedded figure/page so its longest edge is at most this many px (0 disables)",
    )
    args = parser.parse_args()

    if args.work_run_dir:
        result = refresh_from_raw_outputs(
            args.run_out_dir,
            args.work_run_dir,
            figure_mode=args.figures,
            figure_padding=args.figure_padding,
            figure_trim=args.figure_trim,
            figure_max_edge=args.figure_max_edge,
        )
        if args.cleanup_work:
            result["cleanup"] = cleanup_workdir(args.work_run_dir)
    else:
        result = {"message": "No work-run-dir supplied; existing file outputs were not changed."}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
