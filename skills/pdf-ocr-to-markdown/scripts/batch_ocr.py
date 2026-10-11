#!/usr/bin/env python3
"""Batch OCR coordinator for PDFs and image files.

The script performs deterministic local work: validation, PDF text-layer extraction,
page rendering, quality classification, prompt queue generation, page artifacts,
file merges, batch summaries, and cleanup. Vision-model execution is intentionally
not faked; visual routes produce prompts and consume raw visual outputs when they
are present in the expected work directory.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from classify_pages import classify_page, empty_route_counts  # noqa: E402
from cleanup_workdir import cleanup_workdir  # noqa: E402
from extract_pdf_text import (  # noqa: E402
    DependencyIssue,
    count_pdf_pages,
    dependency_status as text_dependency_status,
    extract_pdf_text,
)
from merge_outputs import (  # noqa: E402
    FigureContext,
    _find_page_image,
    build_intended_tree,
    merge_raw_visual_json,
    merge_file_outputs,
    normalize_text_to_markdown,
    page_json_template,
    sidecar_path,
    sidecar_paths,
    write_batch_summary,
    write_json,
    write_page_artifacts,
    write_planned_manifest,
)
from render_pages import (  # noqa: E402
    DEFAULT_VISION_JPEG_QUALITY,
    DEFAULT_VISION_MAX_EDGE,
    dependency_status as render_dependency_status,
    is_image_file,
    render_pdf_pages,
    stage_image_file,
)


PDF_EXTENSIONS = {".pdf"}
DEFAULT_WORK_ROOT = ".ocr_work"
DEFAULT_OUT_ROOT = f"{DEFAULT_WORK_ROOT}/ocr_output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch OCR and structural page routing.")
    parser.add_argument("inputs", nargs="*", help="Input PDF/image files")
    parser.add_argument("--files", nargs="*", default=[], help="Input PDF/image files")
    parser.add_argument("--file-list", default=None, help="Newline-delimited input file list")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_ROOT, help="Internal output root")
    parser.add_argument("--work-dir", default=DEFAULT_WORK_ROOT, help="Temporary work root")
    parser.add_argument("--langs", default="kor+eng", help="OCR language hint for visual prompts")
    parser.add_argument("--dpi", type=int, default=240, help="PDF render DPI")
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
    parser.add_argument(
        "--figures",
        choices=("crop", "page", "placeholder"),
        default="crop",
        help="When a raw page JSON already exists, embed figures as images (crop/page) or keep the text placeholder",
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
    parser.add_argument(
        "--reuse-raw",
        action="store_true",
        help="Copy raw_agent_outputs page files from the most recent previous run under --work-dir before processing",
    )
    parser.add_argument(
        "--raw-from",
        default=None,
        help="Reuse raw_agent_outputs page files from this work-run directory (or raw_agent_outputs directory) instead of auto-detecting",
    )
    parser.add_argument("--workers", type=int, default=2, help="Reserved concurrency setting")
    parser.add_argument("--keep-work", action="store_true", help="Keep intermediate artifacts")
    parser.add_argument("--force-full-vision", action="store_true", help="Route every page to full_vision_ocr")
    parser.add_argument("--no-cleanup", action="store_true", help="Alias for --keep-work")
    parser.add_argument("--auto-install-deps", action="store_true", help="Repair missing Python dependencies before processing")
    parser.add_argument("--dry-run", action="store_true", help="Plan the run without rendering or vision processing")
    return parser.parse_args()


def _strip_list_line(line: str) -> str | None:
    value = line.strip()
    if not value or value.startswith("#") or value.startswith("```"):
        return None
    if value.startswith(("- ", "* ")):
        value = value[2:].strip()
    value = value.strip("`").strip().strip('"').strip("'")
    if not value or value.startswith("#"):
        return None
    return value


def collect_input_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    for item in args.inputs or []:
        paths.append(Path(item).expanduser())
    for item in args.files or []:
        paths.append(Path(item).expanduser())

    if args.file_list:
        list_path = Path(args.file_list).expanduser()
        if not list_path.is_absolute():
            list_path = Path.cwd() / list_path
        base_dir = list_path.parent
        for raw_line in list_path.read_text(encoding="utf-8").splitlines():
            value = _strip_list_line(raw_line)
            if value is None:
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = base_dir / candidate
            paths.append(candidate)

    return paths


def supported_type(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in PDF_EXTENSIONS or is_image_file(path)


def validate_inputs(paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for original in paths:
        resolved = original if original.is_absolute() else Path.cwd() / original
        resolved = resolved.resolve()
        record = {"input": str(original), "path": str(resolved), "name": resolved.name}
        if not resolved.exists():
            failures.append({**record, "error": "file does not exist", "kind": "missing"})
            continue
        if not resolved.is_file():
            failures.append({**record, "error": "path is not a file", "kind": "invalid"})
            continue
        if not supported_type(resolved):
            failures.append({**record, "error": f"unsupported file type: {resolved.suffix}", "kind": "unsupported"})
            continue
        kind = "pdf" if resolved.suffix.lower() == ".pdf" else "image"
        valid.append({**record, "kind": kind})
    return valid, failures


def slugify_filename(filename: str, used: set[str]) -> str:
    path = Path(filename)
    stem = path.stem or path.name or "source"
    extension = path.suffix.lower().lstrip(".")
    raw = f"{stem}_{extension}" if extension else stem
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_-")
    base = base or "source"
    unique_stem = base
    counter = 2
    while base in used:
        base = f"{unique_stem}_{counter}"
        counter += 1
    used.add(base)
    return base


def create_run_dirs(out_root: Path, work_root: Path) -> tuple[str, Path, Path]:
    timestamp = time.strftime("run_%Y%m%d_%H%M%S")
    for index in range(1, 100):
        run_id = timestamp if index == 1 else f"{timestamp}_{index:02d}"
        out_run = out_root / run_id
        work_run = work_root / run_id
        if not out_run.exists() and not work_run.exists():
            out_run.mkdir(parents=True, exist_ok=False)
            work_run.mkdir(parents=True, exist_ok=False)
            return run_id, out_run, work_run
    raise RuntimeError("Could not create a unique run directory after 99 attempts")


def ensure_work_layout(work_run: Path) -> dict[str, Path]:
    dirs = {
        "rendered_pages": work_run / "rendered_pages",
        "extracted_text": work_run / "extracted_text",
        "generated_prompts": work_run / "generated_prompts",
        "raw_agent_outputs": work_run / "raw_agent_outputs",
        "logs": work_run / "logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def dependency_report() -> dict[str, Any]:
    return {
        "text_extraction": text_dependency_status(),
        "rendering": render_dependency_status(),
    }


def _sidecar_path(source_path: Path, suffix: str, run_id: str) -> Path:
    return sidecar_path(source_path, suffix, run_id)


def write_sidecar_outputs(
    source_path: Path,
    output: dict[str, Any],
    run_id: str,
    planned_sidecars: dict[str, str] | None = None,
) -> dict[str, str]:
    sidecars = (
        {key: Path(value) for key, value in planned_sidecars.items()}
        if planned_sidecars
        else sidecar_paths(source_path, run_id)
    )
    shutil.copy2(output["final_md"], sidecars["markdown"])
    return {"markdown": str(sidecars["markdown"])}


def compute_completion_status(errors: list[dict[str, Any]], route_counts: dict[str, int]) -> str:
    if errors:
        return "failed"
    visual_pages = (
        route_counts.get("visual_focus", 0)
        + route_counts.get("verify_text_and_visual", 0)
        + route_counts.get("full_vision_ocr", 0)
    )
    if visual_pages:
        return "text_extraction_complete_visual_pending"
    return "complete"


def ensure_dependencies_installed() -> dict[str, Any]:
    setup_script = SCRIPT_DIR / "setup_dependencies.py"
    command = [sys.executable, str(setup_script), "--install"]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Automatic dependency setup failed. Run this command manually for details: "
            f"{sys.executable} {setup_script} --install\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw_output": result.stdout, "stderr": result.stderr}


def load_prompt_template(route: str) -> str:
    template_path = SKILL_ROOT / "prompts" / f"{route}.md"
    return template_path.read_text(encoding="utf-8")


def write_page_prompt(
    route: str,
    file_slug: str,
    source_file: str,
    page: int,
    text: str,
    image_path: str | None,
    prompt_dir: Path,
    raw_output_dir: Path,
    langs: str,
) -> dict[str, Any]:
    prompt_dir.mkdir(parents=True, exist_ok=True)
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / f"page_{page:04d}.md"
    expected_json = raw_output_dir / f"page_{page:04d}.json"
    expected_md = raw_output_dir / f"page_{page:04d}.md"
    template = load_prompt_template(route)
    image_instruction = (
        f"Attach this DOWNSCALED page preview to the vision-capable model: `{image_path}`. "
        "Use this small JPEG preview, never the full-resolution PNG: a full 240-DPI page "
        "PNG is several MB and can trip HTTP 413 'Payload Too Large'."
        if image_path
        else "No rendered page image is required for this route."
    )
    content = "\n".join(
        [
            template.rstrip(),
            "",
            "## Page Task Context",
            "",
            f"- Source file: `{source_file}`",
            f"- File slug: `{file_slug}`",
            f"- Page: {page}",
            f"- Route: `{route}`",
            f"- OCR language hint: `{langs}`",
            f"- Expected raw JSON output: `{expected_json}`",
            f"- Expected raw Markdown output: `{expected_md}`",
            f"- Image attachment: {image_instruction}",
            "",
            "## Guardrail",
            "",
            "Only write raw visual output after the rendered image has actually been inspected",
            "through an image-capable Codex/tooling path. Do not infer visual annotations,",
            "tables, figures, colors, labels, or emphasis from this prompt or extracted text alone.",
            "",
            "## Extracted Text Layer",
            "",
            "```text",
            text or "",
            "```",
            "",
            "## Required Page JSON Shape",
            "",
            "Return JSON with keys: source_file, page, route, text_quality, visual_density, "
            "content_blocks, text_markdown, visual_annotations, tables, figures, emphasis, "
            "warnings, uncertainties.",
            "",
            "`content_blocks` is the primary final-Markdown source. Return visible page blocks in "
            "natural reading order. Use block objects with keys like type, text, markdown, label, "
            "and level. Do not create diagnostic Markdown sections such as Extracted Text, Visual "
            "Annotations, Tables, or Figures.",
            "",
        ]
    )
    prompt_path.write_text(content, encoding="utf-8")
    return {
        "file_slug": file_slug,
        "source_file": source_file,
        "page": page,
        "route": route,
        "prompt_file": str(prompt_path),
        "image_file": image_path,
        "expected_raw_json": str(expected_json),
        "expected_raw_md": str(expected_md),
    }


def load_raw_visual_output(raw_dir: Path, existing: dict[str, Any]) -> dict[str, Any] | None:
    page = int(existing["page"])
    json_path = raw_dir / f"page_{page:04d}.json"
    md_path = raw_dir / f"page_{page:04d}.md"
    if json_path.exists():
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        merged = merge_raw_visual_json(raw, existing)
        merged["raw_visual_output"] = str(json_path)
        return merged
    if md_path.exists():
        merged = dict(existing)
        merged["text_markdown"] = md_path.read_text(encoding="utf-8").strip()
        merged["raw_visual_output"] = str(md_path)
        return merged
    return None


def build_page_output(
    source_file: str,
    page: int,
    classification: dict[str, Any],
    extracted_text: str,
    raw_dir: Path,
    pages_dir: Path,
    figures: FigureContext | None = None,
) -> dict[str, Any]:
    route = classification["route"]
    warnings = list(classification.get("warnings") or [])
    uncertainties: list[str] = []
    text_markdown = normalize_text_to_markdown(extracted_text)

    if route == "full_vision_ocr" and not text_markdown:
        text_markdown = ""
        uncertainties.append("visible page text has not been OCRed by a vision pass yet")
    if route in {"visual_focus", "verify_text_and_visual"}:
        warnings.append(
            "Visual pass output is pending unless a matching raw_agent_outputs page file exists."
        )
    if route == "full_vision_ocr":
        warnings.append(
            "Vision OCR output is pending unless a matching raw_agent_outputs page file exists."
        )

    page_json = page_json_template(
        source_file=source_file,
        page=page,
        route=route,
        text_quality=classification["text_quality"],
        visual_density=classification["visual_density"],
        text_markdown=text_markdown,
        warnings=warnings,
        uncertainties=uncertainties,
    )
    raw_page = load_raw_visual_output(raw_dir, page_json)
    if raw_page is not None:
        page_json = raw_page
        page_json["warnings"] = [
            warning
            for warning in page_json.get("warnings", [])
            if "output is pending" not in warning
        ]
    write_page_artifacts(pages_dir, page_json, figures)
    return page_json


def write_visual_instructions(metadata_dir: Path, prompt_queue: list[dict[str, Any]], work_run: Path) -> None:
    visual_items = [item for item in prompt_queue if item["route"] != "text_only"]
    lines = [
        "# Visual Pass Instructions",
        "",
        "The Python scripts cannot attach images to a vision-capable model by themselves.",
        "For each non-text-only page, inspect the rendered page image through the",
        "surrounding Codex/runtime image-capable path, then write the result to the",
        "expected raw JSON or Markdown path.",
        "",
            "In Codex, use the local-image inspection capability on the small `.vision.jpg`",
            "preview (resolve relative paths against the directory where batch_ocr.py was run).",
            "Never send the full-resolution `.png` page to the model: at 240 DPI a single page",
            "can be several MB and triggers HTTP 413 'Payload Too Large: Failed to buffer the",
            "request body'. If a page still fails, process it alone in a fresh session.",
            "Do not check for or require tesseract; local OCR engines are not this visual pass.",
        "",
        f"Raw output root: `{work_run / 'raw_agent_outputs'}`",
        "",
    ]
    if not visual_items:
        lines.append("No visual pass is required for this file.")
    else:
        for item in visual_items:
            lines.extend(
                [
                    f"## Page {item['page']:04d} - {item['route']}",
                    "",
                    f"- Prompt: `{item['prompt_file']}`",
                    f"- Image: `{item['image_file']}`",
                    f"- Write JSON: `{item['expected_raw_json']}`",
                    f"- Or write Markdown: `{item['expected_raw_md']}`",
                    "",
                ]
            )
    (metadata_dir / "visual_pass_instructions.md").write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )


def process_file(
    record: dict[str, Any],
    file_slug: str,
    planned_sidecars: dict[str, str],
    out_run: Path,
    work_run: Path,
    work_dirs: dict[str, Path],
    args: argparse.Namespace,
    deps: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    source_path = Path(record["path"])
    source_file = record["name"]
    file_out_dir = out_run / "files" / file_slug
    pages_dir = file_out_dir / "pages"
    metadata_dir = file_out_dir / "metadata"
    pages_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    sidecar_md = planned_sidecars.get("markdown") if planned_sidecars else None
    figure_assets_dir = (
        Path(sidecar_md).parent / f"{Path(sidecar_md).stem}_assets" if sidecar_md else None
    )
    figure_rel_prefix = f"./{Path(sidecar_md).stem}_assets" if sidecar_md else ""

    file_errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    extracted_pages: list[dict[str, Any]] = []
    rendered_pages: list[dict[str, Any]] = []

    if record["kind"] == "pdf":
        if not deps["text_extraction"]["usable"]:
            raise DependencyIssue(
                "No PDF text extraction dependency is installed. Install with: "
                f"{deps['text_extraction']['install']}"
            )
        if not deps["rendering"]["usable_pdf_renderer"]:
            raise RuntimeError(
                "No PDF renderer is installed. Install with: "
                f"{deps['rendering']['install']}"
            )
        extracted_pages = extract_pdf_text(source_path, work_dirs["extracted_text"] / file_slug)
        rendered_pages = render_pdf_pages(
            source_path,
            work_dirs["rendered_pages"] / file_slug,
            args.dpi,
            args.vision_max_edge,
            args.vision_quality,
        )
    else:
        extracted_pages = [{"page": 1, "text": "", "metadata": {"extractor": "image_input"}}]
        rendered_pages = stage_image_file(
            source_path,
            work_dirs["rendered_pages"] / file_slug,
            args.vision_max_edge,
            args.vision_quality,
        )

    rendered_by_page = {int(page["page"]): page for page in rendered_pages}
    route_report: list[dict[str, Any]] = []
    prompt_queue: list[dict[str, Any]] = []
    page_outputs: list[dict[str, Any]] = []
    raw_dir = work_dirs["raw_agent_outputs"] / file_slug
    prompt_dir = work_dirs["generated_prompts"] / file_slug

    for extracted in extracted_pages:
        page_num = int(extracted["page"])
        rendered = rendered_by_page.get(page_num, {})
        classification = classify_page(
            page=page_num,
            text=extracted.get("text") or "",
            metadata=extracted.get("metadata") or {},
            rendered_metadata=rendered,
            force_full_vision=args.force_full_vision,
            is_image=record["kind"] == "image",
        )
        route_report.append(classification)
        prompt_queue.append(
            write_page_prompt(
                route=classification["route"],
                file_slug=file_slug,
                source_file=source_file,
                page=page_num,
                text=extracted.get("text") or "",
                image_path=rendered.get("vision_image") or rendered.get("image_path"),
                prompt_dir=prompt_dir,
                raw_output_dir=raw_dir,
                langs=args.langs,
            )
        )
        figures = None
        if figure_assets_dir is not None:
            figures = FigureContext(
                _find_page_image(work_run, file_slug, page_num),
                figure_assets_dir,
                figure_rel_prefix,
                mode=args.figures,
                padding=args.figure_padding,
                page_label=f"{page_num:04d}",
                trim=args.figure_trim,
                max_edge=args.figure_max_edge,
            )
        page_outputs.append(
            build_page_output(
                source_file=source_file,
                page=page_num,
                classification=classification,
                extracted_text=extracted.get("text") or "",
                raw_dir=raw_dir,
                pages_dir=pages_dir,
                figures=figures,
            )
        )

    manifest = {
        "run_id": out_run.name,
        "source_file": source_file,
        "source_path": str(source_path),
        "file_slug": file_slug,
        "kind": record["kind"],
        "page_count": len(page_outputs),
        "status": "processed",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dpi": args.dpi,
        "langs": args.langs,
        "workers": args.workers,
        "dependency_report": deps,
        "planned_sidecars": planned_sidecars,
    }
    write_json(metadata_dir / "prompt_queue.json", prompt_queue)
    write_visual_instructions(metadata_dir, prompt_queue, work_run)

    output = merge_file_outputs(file_out_dir, manifest, route_report, file_errors)
    output["route_report"] = route_report
    output["prompt_queue"] = prompt_queue
    output["warnings"] = warnings
    return output, file_errors, warnings


def record_failed_file(record: dict[str, Any], file_slug: str, out_run: Path, error: str) -> str:
    file_out_dir = out_run / "files" / file_slug
    metadata_dir = file_out_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    failure = {
        "file": record["path"],
        "source_file": record["name"],
        "file_slug": file_slug,
        "error": error,
    }
    manifest = {
        "run_id": out_run.name,
        "source_file": record["name"],
        "source_path": record["path"],
        "file_slug": file_slug,
        "kind": record.get("kind"),
        "page_count": 0,
        "status": "failed",
    }
    write_json(metadata_dir / "manifest.json", manifest)
    write_json(metadata_dir / "errors.json", [failure])
    (file_out_dir / "quality_report.md").write_text(
        "# Quality Report\n\nProcessing failed before page classification.\n",
        encoding="utf-8",
    )
    return str(metadata_dir / "errors.json")


def print_and_store(progress: list[str], message: str) -> None:
    progress.append(message)
    print(message, flush=True)


def run_dry_run(args: argparse.Namespace, input_paths: list[Path]) -> int:
    valid, validation_failures = validate_inputs(input_paths)
    out_root = Path(args.out_dir)
    work_root = Path(args.work_dir)
    run_id, out_run, work_run = create_run_dirs(out_root, work_root)
    ensure_work_layout(work_run)

    used_slugs: set[str] = set()
    used_sidecar_paths: set[Path] = set()
    file_plans: list[dict[str, Any]] = []
    route_counts = empty_route_counts()
    estimated_pages = 0
    for record in valid:
        source = Path(record["path"])
        slug = slugify_filename(record["name"], used_slugs)
        planned_sidecars = {
            key: str(path)
            for key, path in sidecar_paths(source, run_id, used_sidecar_paths).items()
        }
        if record["kind"] == "image":
            page_count: int | None = 1
            planned_route = "full_vision_ocr"
            route_counts[planned_route] += 1
            estimated_pages += 1
        else:
            page_count = count_pdf_pages(source)
            planned_route = "classify_after_text_extraction"
            if page_count:
                estimated_pages += page_count
        file_plans.append(
            {
                "source_file": record["name"],
                "source_path": record["path"],
                "file_slug": slug,
                "kind": record["kind"],
                "estimated_pages": page_count,
                "planned_route": planned_route,
                "planned_sidecars": planned_sidecars,
            }
        )

    progress = [
        f"[1/9] Input validation complete: {len(valid)} files found, {len(validation_failures)} missing/invalid",
        f"[2/9] Run directory created: {out_run}",
        "[3/9] PDF text extraction complete: dry-run only",
        "[4/9] Page rendering complete: dry-run only",
        "[5/9] Page classification complete: dry-run planned",
        "[6/9] Page-level prompts generated: dry-run only",
        "[7/9] Page-level outputs collected/generated: dry-run only",
        "[8/9] File-level merge complete: dry-run only",
        "[9/9] Cleanup complete: dry-run kept no heavy artifacts",
    ]
    manifest = {
        "run_id": run_id,
        "dry_run": True,
        "input_files": [str(path) for path in input_paths],
        "valid_files": file_plans,
        "failed_files": validation_failures,
        "estimated_pages": estimated_pages,
        "intended_tree": build_intended_tree(run_id, [plan["file_slug"] for plan in file_plans]),
    }
    write_planned_manifest(out_run, manifest)
    summary = {
        "run_id": run_id,
        "input_files": [str(path) for path in input_paths],
        "processed_files": [],
        "failed_files": [failure["path"] for failure in validation_failures],
        "total_pages": estimated_pages,
        "routes": route_counts,
        "completion_status": "dry_run",
        "outputs": [str(out_run / "planned_manifest.json")],
        "sidecar_outputs": [
            {"source_file": plan["source_path"], **plan["planned_sidecars"]}
            for plan in file_plans
        ],
        "warnings": ["dry-run did not render pages or execute vision prompts"],
        "errors": validation_failures,
        "progress": progress,
    }
    write_batch_summary(out_run, summary)
    print("\n".join(progress), flush=True)
    print("\nIntended output tree:\n" + manifest["intended_tree"], flush=True)
    cleanup_workdir(work_run, out_run, keep_work=False, force=True)
    return 0 if not validation_failures else 1


def reuse_previous_raw_outputs(work_root: Path, current_run: Path, dest_raw: Path) -> int:
    """Copy raw_agent_outputs page files from the most recent previous run.

    Enables a single `batch_ocr` pass to render figures from raw page JSON that
    an earlier run (or the vision agent) already produced.
    """

    if not work_root.exists():
        return 0
    runs = sorted(
        (
            path
            for path in work_root.iterdir()
            if path.is_dir() and path != current_run and (path / "raw_agent_outputs").is_dir()
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for run in runs:
        source = run / "raw_agent_outputs"
        copied = copy_raw_tree(source, dest_raw)
        if copied:
            return copied
    return 0


def copy_raw_tree(source: Path, dest_raw: Path) -> int:
    """Copy every ``page_*`` file under ``source`` into ``dest_raw`` (no overwrite)."""

    copied = 0
    if not source.is_dir():
        return 0
    for page_file in source.rglob("page_*"):
        if not page_file.is_file():
            continue
        target = dest_raw / page_file.relative_to(source)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(page_file, target)
        copied += 1
    return copied


def resolve_raw_source(path: Path) -> Path | None:
    """Accept a work-run dir (containing ``raw_agent_outputs/``) or the raw dir itself."""

    if not path.is_dir():
        return None
    nested = path / "raw_agent_outputs"
    if nested.is_dir():
        return nested
    if any(candidate.is_file() for candidate in path.rglob("page_*")):
        return path
    return None


def run(args: argparse.Namespace) -> int:
    if not args.inputs and not args.files and not args.file_list:
        print("error: provide --files or --file-list", file=sys.stderr)
        return 2

    args.keep_work = bool(args.keep_work or args.no_cleanup)
    input_paths = collect_input_paths(args)
    if not input_paths:
        print("error: no input files were provided", file=sys.stderr)
        return 2

    if args.dry_run:
        return run_dry_run(args, input_paths)

    setup_status = None
    if args.auto_install_deps:
        print("[setup] Checking and installing missing local OCR dependencies", flush=True)
        setup_status = ensure_dependencies_installed()

    deps = dependency_report()
    valid, validation_failures = validate_inputs(input_paths)
    out_root = Path(args.out_dir)
    work_root = Path(args.work_dir)
    run_id, out_run, work_run = create_run_dirs(out_root, work_root)
    work_dirs = ensure_work_layout(work_run)
    if args.raw_from:
        source = Path(args.raw_from).expanduser()
        raw_source = resolve_raw_source(source)
        if raw_source is None:
            print(f"[reuse] no raw_agent_outputs page files found under {source}", file=sys.stderr)
        else:
            copied = copy_raw_tree(raw_source, work_dirs["raw_agent_outputs"])
            print(f"[reuse] copied {copied} raw page file(s) from {raw_source}", flush=True)
    elif args.reuse_raw:
        reused = reuse_previous_raw_outputs(work_root, work_run, work_dirs["raw_agent_outputs"])
        if reused:
            print(f"[reuse] copied {reused} raw page file(s) from a previous run", flush=True)

    progress: list[str] = []
    print_and_store(
        progress,
        f"[1/9] Input validation complete: {len(valid)} files found, {len(validation_failures)} missing/invalid",
    )
    print_and_store(progress, f"[2/9] Run directory created: {out_run}")

    used_slugs: set[str] = set()
    processed_files: list[str] = []
    failed_files: list[str] = [failure["path"] for failure in validation_failures]
    batch_errors: list[dict[str, Any]] = list(validation_failures)
    batch_warnings: list[str] = []
    outputs: list[str] = []
    sidecar_outputs: list[dict[str, Any]] = []
    route_counts = empty_route_counts()
    total_pages = 0
    extracted_pages = 0
    rendered_pages = 0
    generated_prompts = 0
    generated_outputs = 0
    merged_files = 0

    used_sidecar_paths: set[Path] = set()
    file_records: list[tuple[dict[str, Any], str, dict[str, str]]] = []
    for record in valid:
        source = Path(record["path"])
        planned_sidecars = {
            key: str(path)
            for key, path in sidecar_paths(source, run_id, used_sidecar_paths).items()
        }
        file_records.append((record, slugify_filename(record["name"], used_slugs), planned_sidecars))

    for record, slug, planned_sidecars in file_records:
        try:
            output, file_errors, warnings = process_file(
                record,
                slug,
                planned_sidecars,
                out_run,
                work_run,
                work_dirs,
                args,
                deps,
            )
            if output is None:
                continue
            processed_files.append(record["path"])
            outputs.extend([output["final_md"], output["final_json"], output["quality_report"]])
            sidecars = write_sidecar_outputs(Path(record["path"]), output, run_id, planned_sidecars)
            output["sidecars"] = sidecars
            sidecar_outputs.append({"source_file": record["path"], **sidecars})
            outputs.extend(sidecars.values())
            route_report = output.get("route_report", [])
            total_pages += int(output.get("pages") or 0)
            extracted_pages += int(output.get("pages") or 0) if record["kind"] == "pdf" else 0
            rendered_pages += int(output.get("pages") or 0)
            generated_prompts += len(output.get("prompt_queue", []))
            generated_outputs += int(output.get("pages") or 0)
            merged_files += 1
            for page in route_report:
                route_counts[page["route"]] += 1
            batch_errors.extend(file_errors)
            batch_warnings.extend(warnings)
        except Exception as exc:
            failed_files.append(record["path"])
            error_path = record_failed_file(record, slug, out_run, str(exc))
            batch_errors.append({"file": record["path"], "error": str(exc), "errors_json": error_path})

    print_and_store(
        progress,
        f"[3/9] PDF text extraction complete: {extracted_pages}/{extracted_pages} pages extracted",
    )
    print_and_store(
        progress,
        f"[4/9] Page rendering complete: {rendered_pages}/{total_pages} pages rendered",
    )
    print_and_store(
        progress,
        "[5/9] Page classification complete: "
        f"text_only={route_counts['text_only']}, "
        f"visual_focus={route_counts['visual_focus']}, "
        f"verify_text_and_visual={route_counts['verify_text_and_visual']}, "
        f"full_vision_ocr={route_counts['full_vision_ocr']}",
    )
    print_and_store(
        progress,
        f"[6/9] Page-level prompts generated: {generated_prompts}/{total_pages} pages",
    )
    print_and_store(
        progress,
        f"[7/9] Page-level outputs collected/generated: {generated_outputs}/{total_pages} pages",
    )
    print_and_store(
        progress,
        f"[8/9] File-level merge complete: {merged_files}/{len(valid)} files",
    )

    completion_status = compute_completion_status(batch_errors, route_counts)
    keep_work_for_visual_pass = completion_status == "text_extraction_complete_visual_pending"
    cleanup = cleanup_workdir(
        work_run,
        out_run,
        keep_work=args.keep_work or keep_work_for_visual_pass,
        force=True,
    )
    print_and_store(
        progress,
        "[9/9] Cleanup complete: "
        f"removed {cleanup['removed_files']} temporary files, "
        f"kept {cleanup.get('final_artifacts')} final artifacts",
    )

    summary = {
        "run_id": run_id,
        "input_files": [str(path) for path in input_paths],
        "processed_files": processed_files,
        "failed_files": failed_files,
        "total_pages": total_pages,
        "routes": route_counts,
        "completion_status": completion_status,
        "outputs": outputs,
        "sidecar_outputs": sidecar_outputs,
        "warnings": batch_warnings,
        "errors": batch_errors,
        "progress": progress,
        "dependency_report": deps,
        "setup_status": setup_status,
        "work_dir": str(work_run) if args.keep_work or keep_work_for_visual_pass else None,
    }
    write_batch_summary(out_run, summary)
    return 0 if not batch_errors else 1


def main() -> int:
    try:
        return run(parse_args())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
