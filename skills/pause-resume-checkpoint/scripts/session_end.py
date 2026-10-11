#!/usr/bin/env python3
"""SessionEnd hook: record the reason a main-thread session ended.

This hook does NOT clean task artifacts. Cleanup is driven by task completion
instead (see ``cleanup_completed_runs.py``). It only appends a durable
session-end note so a later ``codex resume`` knows how the session closed.

The record is written under ``<base>/.codex-progress/session_end.jsonl``,
where ``base`` defaults to the session cwd and can be overridden by a
project-local ``note-plugins.toml`` (``[note_plugins].work_dir``).
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path


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


def _progress_dir(cwd: str | None) -> Path:
    work = _configured_work_dir(cwd)
    if work is None:
        work = Path(cwd) if cwd else Path.cwd()
    return work / ".codex-progress"


def _debug_log(note: str) -> None:
    try:
        target = Path("/tmp/codex-hook-debug")
        target.mkdir(parents=True, exist_ok=True)
        (target / "session_end.json").write_text(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                    "note": note,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def main() -> int:
    event = _read_event()
    _debug_log(f"invoked with event={event!r}")
    cwd = event.get("cwd") or os.getcwd()
    progress_dir = _progress_dir(cwd)
    try:
        progress_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    state_path = progress_dir / "state.json"
    prior_status = "unknown"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(state, dict):
            prior_status = state.get("status", "unknown")
    except (OSError, json.JSONDecodeError):
        pass

    record = {
        "event": "SessionEnd",
        "ended_at": now,
        "reason": event.get("reason", "unknown"),
        "session_id": event.get("session_id", "unknown"),
        "cwd": cwd,
        "progress_dir": str(progress_dir),
        "transcript_path": event.get("transcript_path", ""),
        "prior_task_status": prior_status,
        "unfinished": prior_status in {"interrupted", "in_progress", "pending"},
    }

    log_path = progress_dir / "session_end.jsonl"
    try:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return 0

    # SessionEnd 没有输出 schema，stdout 被忽略；输出一个合法的空对象即可。
    print(json.dumps({}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
