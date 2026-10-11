#!/usr/bin/env python3
"""Cleanup temporary OCR working directories while preserving final outputs."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return 1
    return sum(1 for child in path.rglob("*") if child.is_file())


def count_final_artifacts(out_run_dir: str | Path) -> int:
    return count_files(Path(out_run_dir))


def _is_run_dir(path: Path, parent_name: str) -> bool:
    return path.name.startswith("run_") and path.parent.name == parent_name


def _is_safe_work_run_dir(path: Path) -> bool:
    return _is_run_dir(path, ".ocr_work")


def _is_safe_out_run_dir(path: Path) -> bool:
    return (
        _is_run_dir(path, "ocr_output")
        and path.parent.parent.name == ".ocr_work"
    )


def _require_safe_cleanup_path(path: Path, kind: str, force: bool) -> None:
    if force:
        return
    safe = _is_safe_work_run_dir(path) if kind == "work" else _is_safe_out_run_dir(path)
    if not safe:
        expected = ".ocr_work/run_*" if kind == "work" else ".ocr_work/ocr_output/run_*"
        raise ValueError(
            f"Refusing to delete {path}. Expected a {expected} directory. "
            "Pass force=True only after verifying the path."
        )


def cleanup_workdir(
    work_run_dir: str | Path,
    out_run_dir: str | Path | None = None,
    keep_work: bool = False,
    delete_out_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    work = Path(work_run_dir)
    out_run = Path(out_run_dir) if out_run_dir is not None else None
    final_count = count_final_artifacts(out_run) if out_run is not None else None

    if keep_work:
        return {
            "kept_work": True,
            "removed_files": 0,
            "removed_dirs": 0,
            "work_dir": str(work),
            "out_run_dir": str(out_run) if out_run is not None else None,
            "final_artifacts": final_count,
        }

    _require_safe_cleanup_path(work, "work", force)

    removed_work_files = count_files(work)
    removed_work_dirs = (
        sum(1 for child in work.rglob("*") if child.is_dir()) + 1
        if work.exists()
        else 0
    )
    if work.exists():
        shutil.rmtree(work)

    removed_output_files = 0
    removed_output_dirs = 0
    if delete_out_run and out_run is not None:
        _require_safe_cleanup_path(out_run, "out", force)
        removed_output_files = count_files(out_run)
        removed_output_dirs = (
            sum(1 for child in out_run.rglob("*") if child.is_dir()) + 1
            if out_run.exists()
            else 0
        )
        if out_run.exists():
            shutil.rmtree(out_run)
    removed_files = removed_work_files + removed_output_files
    removed_dirs = removed_work_dirs + removed_output_dirs

    return {
        "kept_work": False,
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "work_dir": str(work),
        "out_run_dir": str(out_run) if out_run is not None else None,
        "deleted_out_run": bool(delete_out_run and out_run is not None),
        "removed_output_files": removed_output_files,
        "final_artifacts": final_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove a run working directory.")
    parser.add_argument("work_run_dir")
    parser.add_argument("--out-run-dir", default=None)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument(
        "--delete-out-run",
        action="store_true",
        help="Remove the internal output run directory after Markdown sidecar outputs are confirmed",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow cleanup outside the guarded .ocr_work run-directory patterns",
    )
    args = parser.parse_args()

    result = cleanup_workdir(
        args.work_run_dir,
        args.out_run_dir,
        args.keep_work,
        args.delete_out_run,
        args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
