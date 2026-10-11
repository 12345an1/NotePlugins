#!/usr/bin/env python3
"""Interrupt hook: persist a resumable progress checkpoint.

Reads the Codex hook event from stdin, extracts the tail of the session
transcript when available, and writes a human-readable checkpoint plus a
machine-readable state file under ``<base>/.codex-progress/``.

``base`` defaults to the session cwd. It can be overridden by a project-local
``note-plugins.toml`` (found by walking up from the session cwd, or via
``$NOTE_PLUGINS_CONFIG``) with ``[note_plugins].work_dir``. The legacy
``~/.config/note-plugins/progress.toml`` (``[progress].base_dir``) is still
accepted. Standard library only; all I/O is fault-tolerant so a hook failure
cannot stall a turn.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path


MAX_TEXT_PREVIEW = 600
MAX_ARG_PREVIEW = 240
MAX_TOOL_CALLS = 5

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
    """Locate the project-local config: env var, then walk up from start, then legacy."""
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
    """Return the configured work region, or None to use the session cwd."""
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
        (target / "save_progress.json").write_text(
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


def _message_text(payload: dict) -> str:
    parts: list[str] = []
    for item in payload.get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts).strip()


def _arguments_preview(arguments) -> str:
    if isinstance(arguments, dict):
        text = json.dumps(arguments, ensure_ascii=False)
    elif isinstance(arguments, str):
        text = arguments
    else:
        text = str(arguments)
    if len(text) > MAX_ARG_PREVIEW:
        return text[:MAX_ARG_PREVIEW] + "…"
    return text


def _extract_transcript_state(transcript_path: str | None) -> dict:
    """Extract the last user/assistant message and recent tool calls."""
    if not transcript_path:
        return {}
    transcript = Path(transcript_path)
    if not transcript.is_file():
        return {}

    last_user = ""
    last_assistant = ""
    tool_calls: list[tuple[str, str]] = []
    try:
        with transcript.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "response_item":
                    continue
                payload = record.get("payload") or {}
                if payload.get("type") == "message":
                    text = _message_text(payload)
                    if not text:
                        continue
                    if payload.get("role") == "user":
                        last_user = text
                    elif payload.get("role") == "assistant":
                        last_assistant = text
                elif payload.get("type") == "function_call":
                    name = payload.get("name") or "unknown"
                    tool_calls.append((name, _arguments_preview(payload.get("arguments"))))
    except OSError:
        pass

    return {
        "last_user_message": last_user[-MAX_TEXT_PREVIEW:],
        "last_assistant_message": last_assistant[-MAX_TEXT_PREVIEW:],
        "recent_tool_calls": tool_calls[-MAX_TOOL_CALLS:],
    }


def _build_checkpoint(state: dict, transcript: dict) -> str:
    lines = [
        "# Progress checkpoint",
        "",
        f"- status: `{state.get('status', 'interrupted')}`",
        f"- saved at: `{state.get('saved_at', '')}`",
        f"- session id: `{state.get('session_id', 'unknown')}`",
        f"- cwd: `{state.get('cwd', '')}`",
        f"- progress dir: `{state.get('progress_dir', '')}`",
        f"- transcript: `{state.get('transcript_path', '')}`",
        "",
        "Resume with `codex resume` (or `--last`).",
        "",
    ]

    if transcript.get("last_user_message"):
        lines += [
            "## Last user message",
            "",
            "```text",
            transcript["last_user_message"],
            "```",
            "",
        ]
    if transcript.get("recent_tool_calls"):
        lines.append("## Recent tool calls")
        lines.append("")
        for name, args in transcript["recent_tool_calls"]:
            lines.append(f"- `{name}`: `{args}`")
        lines.append("")
    if transcript.get("last_assistant_message"):
        lines += [
            "## Last assistant message (preview)",
            "",
            "```text",
            transcript["last_assistant_message"],
            "```",
            "",
        ]

    lines += [
        "## Next steps",
        "",
        "[待续] 从被打断的步骤继续，并在完成后更新或删除本 checkpoint。",
        "",
    ]
    return "\n".join(lines)


def _emit(message: str) -> None:
    # Interrupt 的输出 schema 只允许 systemMessage（不允许顶层 additionalContext）。
    print(json.dumps({"systemMessage": message}, ensure_ascii=False))


def main() -> int:
    event = _read_event()
    _debug_log(f"invoked with event={event!r}")
    cwd = event.get("cwd") or os.getcwd()
    progress_dir = _progress_dir(cwd)
    try:
        progress_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _debug_log(f"mkdir failed: {exc}")
        _emit(f"failed to create {progress_dir}: {exc}")
        return 1

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    transcript_path = event.get("transcript_path", "")
    state = {
        "status": "interrupted",
        "saved_at": now,
        "session_id": event.get("session_id", "unknown"),
        "cwd": cwd,
        "progress_dir": str(progress_dir),
        "transcript_path": transcript_path,
        "hook_event_name": event.get("hook_event_name", "Interrupt"),
    }
    transcript = _extract_transcript_state(transcript_path)
    state["transcript_tail"] = transcript

    try:
        (progress_dir / "checkpoint.md").write_text(
            _build_checkpoint(state, transcript), encoding="utf-8"
        )
        (progress_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        _debug_log(f"write failed: {exc}")
        _emit(f"failed to write checkpoint: {exc}")
        return 1

    summary = f"进度已保存到 {progress_dir / 'checkpoint.md'}"
    _debug_log(f"saved to {progress_dir}")
    if transcript.get("recent_tool_calls"):
        last_name = transcript["recent_tool_calls"][-1][0]
        summary += f"，最近一次工具调用：{last_name}"
    _emit(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
