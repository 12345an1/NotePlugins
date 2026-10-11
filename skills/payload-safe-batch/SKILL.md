---
name: payload-safe-batch
description: "Split a large file or task into small, independent chunks that each run in a fresh session, with a progress ledger, so a single request never trips HTTP 413 'Payload Too Large: Failed to buffer the request body'. Use when a file, merged Markdown, OCR run, or transcript is too big to process in one turn, or after that error appears. Chinese triggers: 请求体过大, Payload Too Large, 分片处理, 大文件分批, 换个会话继续."
---

# Payload-Safe Batch

Turn "too big for one request" work into many small requests that each fit, so a single oversized request cannot abort the turn.

## The failure this fixes

`Payload Too Large: Failed to buffer the request body` is an HTTP 413 returned by the API gateway (or its proxy) **before the model runs**. The request body is the whole conversation so far plus the newest tool result, so one oversized read -- a whole multi-megabyte PDF dump, a merged Markdown/JSON file, or several inlined images -- can exceed the server's body-buffer limit and kill the turn.

Two hard facts to design around:

- **A hook cannot catch it.** Codex hook events (`PreToolUse`, `PostToolUse`, `PreCompact`/`PostCompact`, `SessionStart`/`SessionEnd`, `SubagentStart`/`SubagentStop`, `Interrupt`, `PermissionRequest`) have no "request failed" event, and `Interrupt`/`SessionEnd` do **not** reliably fire on a 413-aborted turn. So "detect the 413 and save" cannot be a hook. The realistic answer is **prevention + a durable progress ledger**, covered here.
- **The limit is not published.** Exact byte/token caps are not exposed, so budget conservatively and let the runner shrink on a real 413.

The closest available early warning is `PreCompact`: Codex compacts when the context is getting large, which is the moment just before a request is most likely to 413. The bundled `pause-resume-checkpoint` hooks wire a checkpoint save to `PreCompact` for exactly this reason.

## The approach

Never put the big thing into one request. Instead:

1. **Inventory the target** with `plan_payload_split.py`. It reads only sizes, page counts, and line offsets -- never the content -- and writes a manifest that references pages/ranges/paths.
2. **Split into chunks** that each stay under a byte budget.
3. **Run each chunk in a fresh session** (`codex exec` per chunk, or a manually pasted command), so the request body contains only that chunk, not the accumulated history.
4. **Update the progress ledger after every chunk** so a 413 mid-run never loses work.
5. **On a real 413, halve the failing chunk and retry** (automatic in the runner).

## Scripts

Both scripts are standard-library only. They live in this skill's `scripts/` directory; run them with a full path from inside the work region (the directory that may hold `.payload_work/`). Below, `scripts/` means this skill's `scripts/`.

### 1. Plan the split

```bash
python3 <skill-dir>/scripts/plan_payload_split.py <files...> --budget-bytes 160000
```

- `<files...>` (or `--files`, `--file-list`): targets. PDFs split by page (`pypdf` -> `pdfplumber` -> regex fallback), images are one unit each, text splits into line ranges.
- `--budget-bytes`: estimated cap on new content per chunk (default 160000).
- `--work-dir`: where the plan, progress ledger, and chunk outputs live (default `.payload_work`).
- Writes `payload_plan.json` (manifest) and `payload_progress.json` (ledger), then prints one ready-to-run command per chunk.

The plan inlines no content; a chunk is a page number, a line range, or a path.

### 2. Run the chunks

```bash
# preview the commands without running anything
python3 <skill-dir>/scripts/run_payload_split.py --plan .payload_work/payload_plan.json

# actually run, one fresh session per chunk, updating progress each time
python3 <skill-dir>/scripts/run_payload_split.py --plan .payload_work/payload_plan.json --execute
```

- Default command per chunk is `codex exec -C <cwd> --skip-git-repo-check -s workspace-write "<prompt>"` -- a **new session** each time.
- `--cmd-template` overrides it. Fields: `{chunk_id}`, `{input_file}`, `{out_file}`, `{units}`, `{prompt}`, `{prompt_q}`, `{cwd}`, `{cwd_q}`.
- After each chunk it rewrites `payload_progress.json` and `<cwd>/.codex-progress/checkpoint.md`.
- On a chunk whose output matches a 413 marker (`payload too large`, `failed to buffer the request body`, `request entity too large`, `413`), it splits that chunk's units in half and retries as `_a`/`_b` sub-chunks, up to `--max-depth` (default 3 = up to 8x smaller).
- Re-running with `--execute` skips chunks already marked `done`, so it resumes where it stopped.

## Agent behavior

When the user hands you something too large, or a turn already died with a 413:

1. Do **not** re-read the whole file into this conversation.
2. Run `plan_payload_split.py` on the target to get the manifest and per-chunk commands.
3. Report the plan (chunk count, budget, first command). Ask whether to run it with `--execute`, or paste the commands yourself one at a time.
4. If you run the chunks yourself, run **one chunk per step** and never `cat` a whole chunk back into context -- read only the small `out_file` afterward.
5. When chunks finish, merge their outputs **on disk** (for example with a script or the OCR `merge_outputs.py`), then report the final path.

Tell the user plainly: the split keeps each request under the gateway limit, and the ledger means an interrupted run resumes instead of restarting.

## Notes on the "new session per task" idea

Running each chunk as its own `codex exec` (or a manually pasted command) is what actually bounds the request body, because a fresh session starts with an empty history. Two caveats:

- Do not fan the chunks back into one conversation as you go; that recreates the oversized request. Keep the big data on disk and pass only small handles (paths, status lines).
- Subagents (`spawn_agent` with no forked turns) give a fresh context too, but the parent still accumulates each subagent's final answer, so keep those answers tiny. A separate process is the cleanest reset.

## Related skills

- `pause-resume-checkpoint`: the checkpoint file this skill updates is the same one the pause/resume hooks use.
- `pdf-ocr-to-markdown`: for large PDFs, plan with this skill first and let the OCR pipeline write per-page artifacts, rather than merging everything into one context.
