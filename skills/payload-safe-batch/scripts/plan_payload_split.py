#!/usr/bin/env python3
"""Plan a payload-safe split for large inputs.

The failure mode this guards against is an HTTP 413 --
``Payload Too Large: Failed to buffer the request body`` -- returned by the API
gateway when a single model request grows past the server's body-buffer limit.
A request body is the whole conversation up to that point plus the newest tool
result, so one oversized read (a whole multi-megabyte PDF dump, a merged
Markdown file, or several inlined images) can blow the limit and abort the turn.

The planner NEVER inlines content. It emits a manifest of small, independent
*chunks* -- each referencing only a page range, a line range, or a single file
path -- plus one ready-to-run ``codex exec`` command per chunk. The companion
``run_payload_split.py`` runs those commands one fresh session at a time and
updates a progress ledger after every chunk, so a mid-run 413 never loses work.

Standard library only. PDF page counts are best-effort
(``pypdf`` -> ``pdfplumber`` -> a conservative regex fallback).

Run from inside the work region (the directory that may hold ``.payload_work/``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BUDGET_BYTES = 160_000
DEFAULT_WORK_DIR = ".payload_work"
# Text is split into small units first, then packed up to --budget-bytes. The
# unit must be well under the budget so a chunk holds several units and the
# runner can halve a chunk that actually trips a 413.
TEXT_UNIT_DIVISOR = 8

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_EXT = {".pdf"}
TEXT_EXT = {
    ".md", ".markdown", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".log",
    ".tex", ".yaml", ".yml", ".py", ".srt", ".vtt", ".html", ".xml",
}


def human_bytes(n: int) -> str:
    step = 1024.0
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < step or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} GiB"


def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PDF_EXT:
        return "pdf"
    if suffix in IMAGE_EXT:
        return "image"
    if suffix in TEXT_EXT:
        return "text"
    # Sniff: mostly-decodable text -> text, otherwise a single opaque unit.
    try:
        sample = path.open("rb").read(4096)
    except OSError:
        return "binary"
    if b"\x00" in sample:
        return "binary"
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


def pdf_page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader  # type: ignore

        return len(PdfReader(str(path)).pages)
    except Exception:
        pass
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(str(path)) as pdf:
            return len(pdf.pages)
    except Exception:
        pass
    try:
        raw = path.open("rb").read()
    except OSError:
        return None
    matches = re.findall(rb"/Type\s*/Page[^s]", raw)
    return len(matches) or None


def _line_units(path: Path, unit_bytes: int) -> list[dict]:
    units: list[dict] = []
    block_bytes = 0
    block_start = 1
    last_line = 0
    with path.open("rb") as handle:
        for lineno, raw in enumerate(handle, start=1):
            last_line = lineno
            block_bytes += len(raw)
            if block_bytes >= unit_bytes:
                units.append(
                    {
                        "ref": f"{path}#L{block_start}-{lineno}",
                        "label": f"lines {block_start}-{lineno}",
                        "est_bytes": block_bytes,
                    }
                )
                block_bytes = 0
                block_start = lineno + 1
    if block_bytes or last_line == 0 or (units and block_start <= last_line):
        if block_start <= last_line or last_line == 0:
            units.append(
                {
                    "ref": f"{path}#L{block_start}-{last_line}",
                    "label": f"lines {block_start}-{last_line}",
                    "est_bytes": block_bytes,
                }
            )
    return units


def _pdf_units(path: Path, budget: int) -> list[dict]:
    size = path.stat().st_size
    pages = pdf_page_count(path)
    if not pages:
        # Cannot split by page; treat the file as one opaque unit.
        return [
            {
                "ref": str(path),
                "label": "whole PDF (page count unknown)",
                "est_bytes": size,
                "warning": "page count unavailable; install pypdf/pdfplumber to split by page",
            }
        ]
    per_page = size // max(pages, 1)
    # Text pages are tiny; scanned pages are image-heavy but the image is passed
    # as a file path. Clamp so heavy scans become one-page chunks and light text
    # pages pack several-per-chunk. The runner re-splits on a real 413 anyway.
    est = min(max(per_page, 2048), budget)
    return [
        {"ref": f"{path}#p{page}", "label": f"page {page}", "est_bytes": est}
        for page in range(1, pages + 1)
    ]


def units_for(path: Path, budget: int) -> tuple[str, list[dict]]:
    kind = detect_kind(path)
    if kind == "image":
        return kind, [
            {
                "ref": str(path),
                "label": f"image {path.name}",
                "est_bytes": path.stat().st_size,
                "warning": "binary image; keep it as a file path, never inline it",
            }
        ]
    if kind == "pdf":
        return kind, _pdf_units(path, budget)
    if kind == "text":
        unit_bytes = max(2048, budget // TEXT_UNIT_DIVISOR)
        return kind, _line_units(path, unit_bytes)
    return kind, [
        {"ref": str(path), "label": f"opaque file {path.name}", "est_bytes": path.stat().st_size}
    ]


def pack_units(kind: str, input_file: Path, units: list[dict], budget: int) -> list[dict]:
    chunks: list[dict] = []
    current: list[dict] = []
    current_bytes = 0

    def flush() -> None:
        nonlocal current, current_bytes
        if not current:
            return
        chunks.append(
            {
                "kind": kind,
                "input_file": str(input_file),
                "units": [u["ref"] for u in current],
                "labels": [u.get("label", u["ref"]) for u in current],
                "est_bytes": current_bytes,
                "oversized": current_bytes > budget,
                "warnings": sorted({u["warning"] for u in current if u.get("warning")}),
            }
        )
        current = []
        current_bytes = 0

    for unit in units:
        if unit["est_bytes"] > budget:
            flush()
            current = [unit]
            current_bytes = unit["est_bytes"]
            flush()
            continue
        if current and current_bytes + unit["est_bytes"] > budget:
            flush()
        current.append(unit)
        current_bytes += unit["est_bytes"]
    flush()
    return chunks


def _prompt_for(chunk: dict, out_file: str) -> str:
    units = ", ".join(chunk["labels"])
    return (
        f"Payload-safe chunk {chunk['chunk_id']}. Input file: {chunk['input_file']}. "
        f"Work ONLY on these units: {units}. "
        f"Read only those units -- do not open the whole file or any other unit. "
        f"Write your result to {out_file} and print a one-line status."
    )


def _preview_command(chunk: dict, cwd: str) -> str:
    prompt_q = shlex.quote(chunk["prompt"])
    return (
        f"codex exec -C {shlex.quote(cwd)} --skip-git-repo-check "
        f"-s workspace-write {prompt_q}"
    )


def _read_inputs(args: argparse.Namespace) -> list[Path]:
    raw: list[str] = [*args.inputs, *args.files]
    if args.file_list:
        list_path = Path(args.file_list).expanduser()
        for line in list_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                raw.append(line)
    seen: set[str] = set()
    paths: list[Path] = []
    for item in raw:
        path = Path(item).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"input not found or not a file: {item}")
        if str(path) in seen:
            continue
        seen.add(str(path))
        paths.append(path)
    if not paths:
        raise SystemExit("no inputs given (pass files, --files, or --file-list)")
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan a payload-safe split of large inputs into small chunks."
    )
    parser.add_argument("inputs", nargs="*", help="Input files")
    parser.add_argument("--files", nargs="*", default=[], help="Input files")
    parser.add_argument("--file-list", default=None, help="Newline-delimited input list")
    parser.add_argument(
        "--budget-bytes",
        type=int,
        default=DEFAULT_BUDGET_BYTES,
        help=f"Max estimated bytes of new content per chunk (default {DEFAULT_BUDGET_BYTES})",
    )
    parser.add_argument("--work-dir", default=DEFAULT_WORK_DIR, help="Work region for plan/progress/chunks")
    parser.add_argument("--out-plan", default=None, help="Plan path (default <work-dir>/payload_plan.json)")
    parser.add_argument(
        "--progress", default=None, help="Progress path (default <work-dir>/payload_progress.json)"
    )
    parser.add_argument("--cwd", default=None, help="cwd baked into the per-chunk command (default: this cwd)")
    parser.add_argument("--json", action="store_true", help="Print the plan JSON to stdout as well")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.budget_bytes < 4096:
        raise SystemExit("--budget-bytes must be >= 4096")
    cwd = os.path.abspath(args.cwd or os.getcwd())
    work_dir = Path(args.work_dir).expanduser()
    work_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = work_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    out_plan = Path(args.out_plan).expanduser() if args.out_plan else work_dir / "payload_plan.json"
    progress_path = Path(args.progress).expanduser() if args.progress else work_dir / "payload_progress.json"

    paths = _read_inputs(args)
    all_chunks: list[dict] = []
    inputs_meta: list[dict] = []
    counter = 0

    for path in paths:
        kind, units = units_for(path, args.budget_bytes)
        inputs_meta.append(
            {
                "path": str(path),
                "kind": kind,
                "bytes": path.stat().st_size,
                "units": len(units),
            }
        )
        for chunk in pack_units(kind, path, units, args.budget_bytes):
            counter += 1
            chunk_id = f"chunk_{counter:04d}"
            out_file = str((chunks_dir / f"{chunk_id}.out.md").resolve())
            chunk["chunk_id"] = chunk_id
            chunk["out_file"] = out_file
            chunk["prompt"] = _prompt_for(chunk, out_file)
            chunk["command"] = _preview_command(chunk, cwd)
            all_chunks.append(chunk)

    plan = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "cwd": cwd,
        "budget_bytes": args.budget_bytes,
        "work_dir": str(work_dir.resolve()),
        "progress_path": str(progress_path.resolve()),
        "inputs": inputs_meta,
        "chunk_count": len(all_chunks),
        "chunks": all_chunks,
        "notes": [
            "This plan references content by range/path only; no document text is inlined.",
            "Each chunk is meant to run in its own fresh session (one at a time).",
            "The real request-body limit is not exposed by the API; the runner halves "
            "any chunk that actually trips a 413 and retries.",
            "Never merge chunk content back into one conversation; merge on disk instead.",
        ],
    }
    out_plan.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    progress = {
        "plan": str(out_plan.resolve()),
        "budget_bytes": args.budget_bytes,
        "updated_at": None,
        "chunks": {
            chunk["chunk_id"]: {
                "status": "pending",
                "input_file": chunk["input_file"],
                "units": chunk["units"],
                "out_file": chunk["out_file"],
            }
            for chunk in all_chunks
        },
    }
    progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"plan:     {out_plan.resolve()}")
    print(f"progress: {progress_path.resolve()}")
    print(f"budget:   {human_bytes(args.budget_bytes)} per chunk")
    print(f"inputs:   {len(paths)}   chunks: {len(all_chunks)}")
    print("")
    for meta in inputs_meta:
        print(f"  [{meta['kind']:6}] {human_bytes(meta['bytes']):>10}  {meta['units']:>5} units  {meta['path']}")
    print("")
    print("Per-chunk commands (each runs in a new session):")
    for chunk in all_chunks:
        flag = "  !oversized" if chunk["oversized"] else ""
        print(f"  {chunk['chunk_id']}  {human_bytes(chunk['est_bytes']):>10}{flag}")
        print(f"      {chunk['command']}")
    if args.json:
        print("")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
