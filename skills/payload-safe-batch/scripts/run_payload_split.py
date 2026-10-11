#!/usr/bin/env python3
"""Run a payload-safe chunk plan one fresh session at a time.

Reads the manifest produced by ``plan_payload_split.py`` and executes one
command per chunk. By default each chunk is a ``codex exec`` invocation, i.e. a
brand-new session whose request body contains only that chunk's small unit --
never the accumulated history. After every chunk the progress ledger
``payload_progress.json`` is updated and a human-readable checkpoint is written,
so if a chunk still trips ``Payload Too Large: Failed to buffer the request
body`` the run can be resumed exactly where it stopped.

Adaptive recovery: when a chunk fails and its output contains a 413 / "payload
too large" marker, the chunk's units are cut in half and each half is retried as
a new sub-chunk, up to ``--max-depth`` levels.

Nothing is executed unless ``--execute`` is passed; otherwise the commands are
printed so you or the agent can run them manually.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PAYLOAD_MARKERS = (
    "payload too large",
    "failed to buffer the request body",
    "request entity too large",
    "content too large",
    "413",
)

DEFAULT_TEMPLATE = (
    "codex exec -C {cwd_q} --skip-git-repo-check -s workspace-write {prompt_q}"
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _chunk_order(chunk_id: str) -> tuple:
    head = chunk_id.split("_")[1] if "_" in chunk_id else chunk_id
    numeric = "".join(ch for ch in head if ch.isdigit())
    return (int(numeric) if numeric else 0, chunk_id)


def _template_fields(chunk: dict, cwd: str) -> dict:
    prompt = chunk.get("prompt") or ""
    return {
        "chunk_id": chunk["chunk_id"],
        "input_file": chunk["input_file"],
        "out_file": chunk["out_file"],
        "units": " ".join(chunk["units"]),
        "prompt": prompt,
        "prompt_q": shlex.quote(prompt),
        "cwd": cwd,
        "cwd_q": shlex.quote(cwd),
    }


def _render_command(chunk: dict, cwd: str, template: str) -> str:
    return template.format(**_template_fields(chunk, cwd))


def _has_payload_marker(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in PAYLOAD_MARKERS)


def _write_progress(progress_path: Path, progress: dict) -> None:
    progress["updated_at"] = _now()
    progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_checkpoint(path: Path, plan_path: Path, work_dir: Path, progress: dict) -> None:
    statuses = {cid: c["status"] for cid, c in progress["chunks"].items()}
    done = sum(1 for s in statuses.values() if s == "done")
    failed = [cid for cid, s in statuses.items() if s == "failed"]
    pending = [cid for cid, s in statuses.items() if s == "pending"]
    superseded = [cid for cid, s in statuses.items() if s == "split"]
    active_total = done + len(failed) + len(pending)
    lines = [
        "# Payload-safe batch checkpoint",
        "",
        f"- goal: process a large input as small fresh-session chunks (no 413)",
        f"- plan: `{plan_path}`",
        f"- work dir: `{work_dir}`",
        f"- updated: {_now()}",
        f"- progress: {done}/{active_total} chunks done"
        + (f", {len(failed)} failed" if failed else "")
        + (f", {len(pending)} pending" if pending else "")
        + (f", {len(superseded)} split into smaller chunks" if superseded else ""),
        "",
        "## Next step",
    ]
    if pending:
        lines.append(f"- run: `python3 run_payload_split.py --plan {plan_path} --execute` (resumes at {pending[0]})")
    elif failed:
        lines.append(f"- failed chunks: {', '.join(failed)} -- lower --budget-bytes or inspect their logs")
    else:
        lines.append("- all chunks done; merge chunk outputs on disk")
    lines += [
        "",
        "## Recovery note",
        "If a turn died with `Payload Too Large: Failed to buffer the request body`, the",
        "request body exceeded the API gateway buffer. Do NOT reload the whole file into one",
        "session; resume this chunk plan instead, or lower `--budget-bytes` and re-plan.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _split_chunk(chunk: dict, chunks_dir: Path) -> list[dict]:
    units = chunk["units"]
    labels = chunk.get("labels", units)
    mid = max(1, len(units) // 2)
    parts: list[dict] = []
    for suffix, unit_slice, label_slice in (
        ("a", units[:mid], labels[:mid]),
        ("b", units[mid:], labels[mid:]),
    ):
        if not unit_slice:
            continue
        new_id = f"{chunk['chunk_id']}_{suffix}"
        out_file = str((chunks_dir / f"{new_id}.out.md").resolve())
        parts.append(
            {
                "chunk_id": new_id,
                "kind": chunk.get("kind", "text"),
                "input_file": chunk["input_file"],
                "units": list(unit_slice),
                "labels": list(label_slice),
                "est_bytes": chunk["est_bytes"] // 2,
                "oversized": False,
                "warnings": chunk.get("warnings", []),
                "out_file": out_file,
                "prompt": chunk.get("prompt", ""),
            }
        )
    return parts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a payload-safe chunk plan.")
    parser.add_argument("--plan", required=True, help="Path to payload_plan.json")
    parser.add_argument("--progress", default=None, help="Progress ledger (default: from plan)")
    parser.add_argument("--work-dir", default=None, help="Work dir (default: from plan)")
    parser.add_argument("--cwd", default=None, help="Working root baked into each command")
    parser.add_argument("--cmd-template", default=DEFAULT_TEMPLATE, help="Command template per chunk")
    parser.add_argument("--execute", action="store_true", help="Actually run chunks (default: preview only)")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep going after a hard failure")
    parser.add_argument("--max-depth", type=int, default=3, help="Max 413 re-splits per chunk lineage")
    parser.add_argument("--timeout", type=int, default=1800, help="Per-chunk timeout seconds")
    parser.add_argument("--max-chunks", type=int, default=500, help="Safety cap on total chunks run")
    parser.add_argument("--checkpoint", default=None, help="Checkpoint path (default <cwd>/.codex-progress/checkpoint.md)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan_path = Path(args.plan).expanduser().resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    cwd = os.path.abspath(args.cwd or plan.get("cwd") or os.getcwd())
    work_dir = Path(args.work_dir).expanduser() if args.work_dir else Path(plan["work_dir"])
    chunks_dir = work_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    progress_path = (
        Path(args.progress).expanduser()
        if args.progress
        else Path(plan.get("progress_path", work_dir / "payload_progress.json"))
    )
    if progress_path.is_file():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    else:
        progress = {
            "plan": str(plan_path),
            "budget_bytes": plan.get("budget_bytes"),
            "updated_at": None,
            "chunks": {
                c["chunk_id"]: {
                    "status": "pending",
                    "input_file": c["input_file"],
                    "units": c["units"],
                    "out_file": c["out_file"],
                }
                for c in plan["chunks"]
            },
        }
    checkpoint_path = (
        Path(args.checkpoint).expanduser()
        if args.checkpoint
        else Path(cwd) / ".codex-progress" / "checkpoint.md"
    )

    worklist = sorted((dict(c) for c in plan["chunks"]), key=lambda c: _chunk_order(c["chunk_id"]))
    executed = 0
    index = 0

    while index < len(worklist):
        chunk = worklist[index]
        index += 1
        cid = chunk["chunk_id"]
        entry = progress["chunks"].get(cid)
        if entry and entry["status"] == "done":
            continue
        if executed >= args.max_chunks:
            print(f"!! stopped: reached --max-chunks {args.max_chunks}")
            break

        command = _render_command(chunk, cwd, args.cmd_template)
        if not args.execute:
            print(f"[preview] {cid}: {command}")
            continue

        executed += 1
        print(f"[run] {cid} ({len(chunk['units'])} unit(s))")
        log_path = chunks_dir / f"{cid}.log"
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=args.timeout,
            )
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            rc = proc.returncode
        except subprocess.TimeoutExpired as exc:
            combined = f"TIMEOUT after {args.timeout}s\n{exc.stdout or ''}\n{exc.stderr or ''}"
            rc = 124
        log_path.write_text(combined, encoding="utf-8")

        depth = cid.count("_a") + cid.count("_b")
        if rc == 0:
            status = "done"
        elif _has_payload_marker(combined) and len(chunk["units"]) > 1 and depth < args.max_depth:
            parts = _split_chunk(chunk, chunks_dir)
            for part in parts:
                progress["chunks"][part["chunk_id"]] = {
                    "status": "pending",
                    "input_file": part["input_file"],
                    "units": part["units"],
                    "out_file": part["out_file"],
                }
            worklist[index:index] = parts
            status = "split"
            print(f"     payload-too-large -> split into {', '.join(p['chunk_id'] for p in parts)}")
        else:
            status = "failed"
            print(f"     failed rc={rc}; log: {log_path}")
            if not args.continue_on_error:
                progress["chunks"][cid] = {
                    "status": status,
                    "input_file": chunk["input_file"],
                    "units": chunk["units"],
                    "out_file": chunk["out_file"],
                    "log": str(log_path),
                    "returncode": rc,
                }
                _write_progress(progress_path, progress)
                _write_checkpoint(checkpoint_path, plan_path, work_dir, progress)
                print(f"!! stopping on failure; resume with the same command")
                return 1

        progress["chunks"][cid] = {
            "status": status,
            "input_file": chunk["input_file"],
            "units": chunk["units"],
            "out_file": chunk["out_file"],
            "log": str(log_path),
            "returncode": rc,
        }
        _write_progress(progress_path, progress)
        _write_checkpoint(checkpoint_path, plan_path, work_dir, progress)

    statuses = [c["status"] for c in progress["chunks"].values()]
    done = sum(1 for s in statuses if s == "done")
    active_total = sum(1 for s in statuses if s in ("done", "failed", "pending"))
    failed = [cid for cid, c in progress["chunks"].items() if c["status"] == "failed"]
    print("")
    print(f"done: {done}/{active_total}   failed: {len(failed)}")
    if failed:
        print(f"failed chunks: {', '.join(failed)}")
    if not args.execute:
        print("(preview only; re-run with --execute to actually run chunks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
