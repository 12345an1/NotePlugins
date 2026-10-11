---
name: pause-resume-checkpoint
description: "Keep an explicit, resumable progress checkpoint for long multi-step tasks so work can be stopped and later continued without losing state. Use when the user says 暂停/停止/保存进度/继续/恢复, or when a task may be interrupted and resumed."
---

# Pause-Resume Checkpoint

This is a thin protocol skill. It does not change how the task itself is done; it only guarantees that in-progress work can be stopped and resumed without losing state.

## Checkpoint location

Use a stable, writable, project-local path. Never use `/tmp`, because temporary directories can be cleared and may not be visible to a later session.

```text
<project>/.codex-progress/checkpoint.md
<project>/.codex-progress/state.json   # optional, machine-readable
```

Create the directory when it does not exist. The pause step and the resume step MUST read and write the same paths.

Note: this skill intentionally writes to `.codex-progress`, not `.codex/progress`, because some projects mount `.codex` read-only (for example a plugin repo whose `.codex` is protected). `.codex-progress` is a normal directory and stays writable in the task cwd.

### Project config (work region)

Put `note-plugins.toml` in the Codex working directory (or a parent). The hooks find it by walking up from the session cwd, or via `$NOTE_PLUGINS_CONFIG`.

```toml
[note_plugins]
# Everything note-plugins writes lives under this directory:
#   <work_dir>/.codex-progress   checkpoints + session-end log
#   <work_dir>/.ocr_work         OCR run work + raw visual output
# Relative paths resolve against this file's directory.
work_dir = "."
```

Use a writable `work_dir` when the project cwd is read-only. Run the OCR pipeline from inside `work_dir` so its `.ocr_work/` and the Markdown sidecar land in the same region. When no config is present the work region falls back to the session cwd. The legacy `~/.config/note-plugins/progress.toml` (`[progress].base_dir`) is still accepted.

## Save protocol

Treat these trigger words as a save-and-stop request: `暂停`, `停止`, `保存进度`, `pause`, `stop`.

When any of them appears:

1. Before stopping, write or update `checkpoint.md` with at least:
   - the goal and current phase;
   - what is done so far;
   - the exact next step or steps;
   - open questions or blockers;
   - key files, environment details, or variables a later run would need.
2. Write `state.json` only when a scripted resumer needs structured data. Keep it a subset of `checkpoint.md` so the Markdown file remains the source of truth.
3. Reply with the checkpoint path and a one-line summary of what was saved, then stop. Do not start new work after saving.

Also update the checkpoint incrementally after each completed sub-step, so an unexpected stop never loses much. Do not wait for an explicit pause command to write the very first snapshot.

## Resume protocol

Treat `继续`, `恢复`, `resume`, `continue` as resume requests.

1. Read `checkpoint.md` and, if present, `state.json`.
2. Restate the saved state in one or two lines, then continue from the exact next step.
3. If the checkpoint is missing, say so and reconstruct state from the conversation transcript instead of guessing.

## Bundled hook scripts

This skill ships three thin hook scripts under `scripts/`. They are standard-library only and are run with system `python3` (they are hook infrastructure, not the NoteWorkplace OCR/PPT pipeline):

- `scripts/save_progress.py` -> wire to the `Interrupt` hook. It writes `<cwd>/.codex-progress/checkpoint.md` and `state.json` when the user interrupts with `/stop` or `Esc`, and reads the session transcript (when `transcript_path` is provided) to record the last user message, recent tool calls, and last assistant message.
- `scripts/session_end.py` -> wire to the `SessionEnd` hook. It appends a session-end record to `<cwd>/.codex-progress/session_end.jsonl`, including `prior_task_status` and an `unfinished` flag. It deliberately does NOT clean task artifacts.
- `scripts/cleanup_completed_runs.py` -> wire to the `PostToolUse` hook. It cleans an OCR run only when the task itself finished, i.e. `batch_summary.json` reports `completion_status == "complete"`, and leaves the Markdown sidecar in place. Pending and failed runs are never removed.

### Wiring the hooks

Codex reads project hooks from the `[hooks]` table in `<project>/.codex/config.toml` (not from `.codex/hooks.toml`). A ready-to-copy example is in `hooks.toml.example`; copy its `[hooks]` table into `<project>/.codex/config.toml`. The `PostToolUse` matcher uses `shell|local_shell|exec_command|Bash`; if your Codex version names its shell tool differently, adjust the matcher after the first real run.

When this skill ships as a plugin, the hooks are declared in `hooks/hooks.json` and use `${PLUGIN_ROOT}/skills/pause-resume-checkpoint/scripts/...` so they resolve inside the plugin root.

Two event facts worth remembering:

- `Interrupt` and `SessionEnd` are main-thread only; they do not run for subagents.
- Cleanup belongs to task completion (`PostToolUse` after `batch_ocr.py`/`merge_outputs.py`), NOT to `SessionEnd`, because a session can end while other tasks are still pending. A text-only OCR pass is NOT "complete" until the visual pass is done; until then the run stays `text_extraction_complete_visual_pending` and is left untouched.

### Payload limit (`Payload Too Large`) and early-warning saves

`Payload Too Large: Failed to buffer the request body` is an HTTP 413 from the API gateway: the request body (all prior messages plus the newest tool result) exceeded the server's buffer. Two consequences for this skill:

- **No hook fires on the 413 itself.** There is no "request failed" hook event, and `Interrupt`/`SessionEnd` do not reliably run when a turn aborts on a 413. So you cannot rely on the interrupt/session-end hooks to save progress in that case. Prevention and continuous snapshots are what protect the work.
- **`PreCompact` is the early warning.** Codex compacts when the context is getting large -- the moment just before a request is most likely to 413. Wiring `save_progress.py` to `PreCompact` (see `hooks.toml.example`) snapshots the checkpoint before compaction, so if the next request aborts, a later session can still resume from the latest checkpoint.

When a target is simply too large to process in one turn, do not re-read it into the conversation. Use the `payload-safe-batch` skill: it splits the target into small chunks that each run in a fresh session and updates the same `.codex-progress/checkpoint.md` after every chunk.

## Automatic saves (optional)

For a manual-only fallback, a scheduled cron/systemd timer can invoke an independent script, but prefer it only for periodic checks or reminders. It is not good for snapshotting a stateful agent turn, because a scheduled `codex exec` run is expensive and does not continue the same in-progress turn.
