---
name: note-postprocess
description: Use after courseware notes are already drafted, when the remaining work is cleanup instead of extraction — removing redundant duplicate headings, repairing links, restructuring navigation, or validating a whole Obsidian note vault. Chinese triggers include 笔记去重, 重复大标题, 去掉重复标题, 整理笔记库, 链接修复, 导航整理, 成稿后清理. Do not use this skill while slides are still being extracted or notes are still being rewritten; use $ppt-to-md-for-obsidian for that stage.
---

# Note Postprocess

## Runtime environment (note-plugins override)

This installed copy is part of the `note-plugins` collection. Run every bundled Python script with the `NoteWorkplace` conda environment, not system `python3` or `base`.

Preferred interpreter:

```text
/home/<你的用户名>/Programs/miniconda3/envs/NoteWorkplace/bin/python
```

If that binary is missing, verify that `conda run` resolves to the same environment before running anything; do not silently fall back to `/usr/bin/python`. The top-level `courseware-to-notes` skill contains the required check.

## Scope

This skill owns the notes **after** they have been drafted. It takes over where `$ppt-to-md-for-obsidian` stops: the source has already been extracted and rewritten, and what remains is cleanup and validation of the vault itself.

Work that belongs here:

- removing redundant duplicate headings in finished notes,
- repairing links and removing repeated trailing link dumps,
- restructuring navigation pages and cross-note ordering,
- vault-wide validation of an existing note tree.

Work that does **not** belong here:

- extracting or re-extracting PPT/PPTX/PDF sources,
- reconstructing slide order,
- the first full rewrite of a course into notes.

Keep source files read-only. Load project-local guidance (`AGENT.md`, `agent.md`, `agent/`) first and treat it as the source of truth when it is stricter than this skill.

## Duplicate heading cleanup

Drafted notes usually pick up repeated big headings: the note title (`# 经验风险与泛化`) is echoed by the section heading right under it, or a slide banner copied onto every slide becomes the same section title twice in a row. Run the bundled script to find and remove them.

```bash
# report only; exit 1 when any note would change
"$NOTE_PY" "$PLUGIN_REPO/skills/note-postprocess/scripts/dedupe_headings.py" \
  "notes/课程名" --check

# fix in place (default)
"$NOTE_PY" "$PLUGIN_REPO/skills/note-postprocess/scripts/dedupe_headings.py" \
  "notes/课程名"

# preview the diff-like report without writing
"$NOTE_PY" "$PLUGIN_REPO/skills/note-postprocess/scripts/dedupe_headings.py" \
  "notes/课程名" --dry-run
```

Useful options: `--json` for machine-readable output, `--ext .md` to change the file filter, `--no-recursive` to stay in one directory.

A heading is removed only when all three hold:

1. its text matches an earlier heading after normalization (full-width/half-width, case, surrounding whitespace, trailing `#`, explicit `{#id}`, and a trailing colon are ignored),
2. nothing but blank lines sits between the two headings — no body content, no bullets, no formulas,
3. the later heading is at the same level or deeper than the earlier one.

Everything else is left alone on purpose. The same title separated by real content is kept, because two different sections may legitimately share a heading; a shallower heading that reopens a section after a deeper one is kept as well. YAML frontmatter and fenced code blocks are never treated as headings, and the tool is idempotent.

Removing a heading is not a rewrite: the surrounding content stays exactly where it was. If a run needs to merge two sections rather than drop a duplicate heading, or the duplicate carries different content, edit the note by hand instead of forcing the script.

The script only recognizes ATX headings (`## 标题`). A section title written as bold text or a plain line is invisible to both the Obsidian outline and this dedup pass; normalize it to the `##` form first.

Slide-banner repeats inside the raw extraction (`## Slide 1: 绪论`, `## Slide 2: 绪论`) are a different problem: they are extraction noise with different heading text, so this script does not touch them. Handle them in the `ppt-to-md-for-obsidian` pipeline before rewriting.

## Link repair and validation

Reuse the validators bundled with `ppt-to-md-for-obsidian` rather than re-implementing them:

```bash
"$NOTE_PY" "$PLUGIN_REPO/skills/ppt-to-md-for-obsidian/scripts/check_obsidian_links.py" "notes/课程名"
"$NOTE_PY" "$PLUGIN_REPO/skills/ppt-to-md-for-obsidian/scripts/check_course_notes.py" "notes/课程名"
```

Order of operations for a cleanup pass:

1. run `dedupe_headings.py --check` and review the reported headings,
2. fix with `dedupe_headings.py`,
3. repair the links the duplicate removal may have orphaned,
4. re-run the link and course-note checks above,
5. report what changed and what still needs manual review.

## Bundled Resources

- `scripts/dedupe_headings.py`: remove redundant duplicate headings from drafted Markdown notes (`--check` / `--dry-run` / default fix in place).
- `tests/test_dedupe_headings.py`: regression tests for the dedup rules; run with the `NoteWorkplace` interpreter (install `pytest` into that environment first if it is missing).
