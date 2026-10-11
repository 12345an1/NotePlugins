#!/usr/bin/env python3
"""PostToolUse hook: clean a completed OCR run, leaving the Markdown sidecar.

Trigger this hook after shell commands that finish a PDF/image task. It scans
``<work_base>/.ocr_work/ocr_output/run_*`` and deletes only runs whose
``batch_summary.json`` reports ``completion_status == "complete"``, using the
bundled ``cleanup_workdir.py`` helper (which refuses unsafe paths). Pending or
failed runs are left untouched.

``work_base`` is the project-local ``note-plugins.toml`` ``[note_plugins].work_dir``
(found by walking up from the session cwd), falling back to the session cwd.
The hook stays silent when there is nothing to clean.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path


TARGET_SCRIPTS = ("batch_ocr.py", "merge_outputs.py")
# 0.160.1 里 shell 工具名可能是 shell / local_shell / exec_command；保留 Bash 兼容旧版本。
SHELL_TOOL_NAMES = {"shell", "local_shell", "exec_command", "Bash"}
# Interpreter used to run the bundled OCR/PPT helpers. Resolved dynamically so
# the plugin ships no machine-specific path: explicit env override first, then a
# NoteWorkplace conda env if one is active or discoverable, then the current
# interpreter.
_NOTE_PYTHON_ENV_KEYS = ("NOTE_PLUGINS_PYTHON", "NOTEWORKPLACE_PYTHON")


def _resolve_noteworkplace_python() -> str:
    for key in _NOTE_PYTHON_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            candidate = Path(value).expanduser()
            if candidate.exists():
                return str(candidate)
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix and Path(conda_prefix).name == "NoteWorkplace":
        candidate = Path(conda_prefix) / "bin" / "python"
        if candidate.exists():
            return str(candidate)
    for candidate in (
        Path.home() / "miniconda3" / "envs" / "NoteWorkplace" / "bin" / "python",
        Path.home() / "Programs" / "miniconda3" / "envs" / "NoteWorkplace" / "bin" / "python",
        Path.home() / ".conda" / "envs" / "NoteWorkplace" / "bin" / "python",
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


NOTEWORKPLACE_PYTHON = _resolve_noteworkplace_python()
CLEANUP_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "skills"
    / "pdf-ocr-to-markdown"
    / "scripts"
    / "cleanup_workdir.py"
)

CONFIG_NAME = "note-plugins.toml"
_CONFIG_ENV_KEYS = ("NOTE_PLUGINS_CONFIG", "NOTE_PLUGINS_PROGRESS_CONFIG")
_LEGACY_CONFIG = Path.home() / ".config" / "note-plugins" / "progress.toml"


def _read_event() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _find_config(start: str | Path | None) -> Path | None:
    for key in _CONFIG_ENV_KEYS:
        env_value = os.environ.get(key)
        if env_value:
            candidate = Path(env_value).expanduser()
            if candidate.is_file():
                return candidate
    base = Path(start) if start else Path.cwd()
    try:
        base = base.resolve()
    except OSError:
        pass
    for directory in [base, *base.parents]:
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return _LEGACY_CONFIG if _LEGACY_CONFIG.is_file() else None


def _configured_work_dir(start: str | Path | None) -> Path | None:
    config = _find_config(start)
    if config is None:
        return None
    try:
        with config.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    raw = ""
    section = data.get("note_plugins")
    if isinstance(section, dict):
        raw = str(section.get("work_dir") or section.get("cwd") or "").strip()
    if not raw:
        legacy_section = data.get("progress")
        if isinstance(legacy_section, dict):
            raw = str(legacy_section.get("base_dir") or "").strip()
    if not raw:
        return None
    resolved = Path(raw).expanduser()
    if not resolved.is_absolute():
        resolved = config.parent / resolved
    return resolved


def _tool_command(tool_input) -> str:
    if isinstance(tool_input, str):
        return tool_input
    if isinstance(tool_input, dict):
        for key in ("command", "cmd", "shell_command"):
            value = tool_input.get(key)
            if isinstance(value, str):
                return value
    return ""


def _is_target_tool(event: dict) -> bool:
    if event.get("tool_name") not in SHELL_TOOL_NAMES:
        return False
    command = _tool_command(event.get("tool_input"))
    return any(script in command for script in TARGET_SCRIPTS)


def _run_dir_ids(work_base: Path) -> list[str]:
    out_root = work_base / ".ocr_work" / "ocr_output"
    if not out_root.is_dir():
        return []
    ids: list[str] = []
    for child in sorted(out_root.iterdir()):
        if child.is_dir() and child.name.startswith("run_"):
            ids.append(child.name)
    return ids


def _completion_status(work_base: Path, run_id: str) -> str | None:
    summary = work_base / ".ocr_work" / "ocr_output" / run_id / "batch_summary.json"
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    status = data.get("completion_status")
    return status if isinstance(status, str) else None


def _cleanup_run(work_base: Path, run_id: str) -> dict:
    work_run = work_base / ".ocr_work" / run_id
    out_run = work_base / ".ocr_work" / "ocr_output" / run_id
    python = NOTEWORKPLACE_PYTHON if Path(NOTEWORKPLACE_PYTHON).exists() else sys.executable
    command = [
        python,
        str(CLEANUP_SCRIPT),
        str(work_run),
        "--out-run-dir",
        str(out_run),
        "--delete-out-run",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"removed": False, "run_id": run_id, "error": str(exc)}
    if result.returncode == 0:
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return {"removed": True, "run_id": run_id}
    return {
        "removed": False,
        "run_id": run_id,
        "stderr": result.stderr[-500:],
    }


def _emit(message: str) -> None:
    # PostToolUse 的输出用 hookSpecificOutput.additionalContext 注入上下文。
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": message,
                }
            },
            ensure_ascii=False,
        )
    )


def main() -> int:
    event = _read_event()
    if not _is_target_tool(event):
        # Not a relevant shell command; do nothing and stay silent-fast.
        return 0

    cwd = event.get("cwd") or os.getcwd()
    work_base = _configured_work_dir(cwd) or Path(cwd)
    completed = [
        run_id
        for run_id in _run_dir_ids(work_base)
        if _completion_status(work_base, run_id) == "complete"
    ]
    if not completed:
        # Silent when there is nothing to clean (avoids per-command noise).
        return 0

    results = [_cleanup_run(work_base, run_id) for run_id in completed]
    cleaned = [result for result in results if result.get("removed", True)]
    _emit(f"cleaned {len(cleaned)} completed OCR run(s): " + ", ".join(completed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
