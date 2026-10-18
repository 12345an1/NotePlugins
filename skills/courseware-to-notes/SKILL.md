---
name: courseware-to-notes
description: "Top-level thin skill for this note-plugins collection. Route teacher courseware files into editable Markdown notes and enforce that every script runs inside the NoteWorkplace conda environment. Use when the user says 把课件转成笔记, PPT/PDF转Markdown, 整理老师课件, or similar."
---

# Courseware To Notes

This is the thin top-level skill for the note-plugins collection. It does not reimplement extraction itself; it routes work to the right skill and enforces the shared runtime rule.

## Shared runtime rule

All Python programs in this collection MUST be executed inside the `NoteWorkplace` conda virtual environment. Never use the `base` environment or system Python for this plugin's scripts.

Environment location:

```text
/home/<你的用户名>/Programs/miniconda3/envs/NoteWorkplace
```

Preferred interpreter:

```text
/home/<你的用户名>/Programs/miniconda3/envs/NoteWorkplace/bin/python
```

When the direct interpreter is missing, do not silently fall back to system Python. First verify that conda actually resolves to the expected environment:

```bash
conda run --no-capture-output -n NoteWorkplace python -c \
  'import sys; assert sys.executable.startswith("/home/<你的用户名>/Programs/miniconda3/envs/NoteWorkplace/")'
```

Only after that check passes may you run a script through conda:

```bash
conda run --no-capture-output -n NoteWorkplace python <script> <args...>
```

If the check fails, install Python into the environment first:

```bash
conda install -n NoteWorkplace python pip
```

Do not silently fall back to `python3` from `$PATH` or from `base`.

## Skill routing

1. `.ppt` / `.pptx` / `.pptm` courseware -> use `ppt-to-md-for-obsidian`.
   - For a repeatable full run, call `scripts/ppt_to_obsidian_pipeline.py <source>` with the `NoteWorkplace` interpreter.
   - For a quick deterministic text dump, call `scripts/extract_pptx_text.py <file.pptx>`.
   - For legacy `.ppt`, pass `--soffice /path/to/soffice` or let the pipeline try the detected LibreOffice binary.
2. `.pdf` and image courseware -> use `pdf-ocr-to-markdown`.
   - Run its bundled pipeline with the `NoteWorkplace` interpreter.
   - Respect that skill's vision-honesty rule: do not claim OCR complete when visual pages are still pending.
3. Formula-heavy pages or physics/math review -> use `math-physics-complete`.
   - Apply its LaTeX/rendering, symbol-table, units, and boundary-condition contract.
4. Notes already drafted, only cleanup left -> use `note-postprocess`.
   - Remove redundant duplicate big headings with `skills/note-postprocess/scripts/dedupe_headings.py <notes-dir> --check`, then run it again without `--check` to fix.
   - Reuse the checkers in `skills/ppt-to-md-for-obsidian/scripts/` (`check_obsidian_links.py`, `check_course_notes.py`) for link and structure validation instead of writing new ones.

## Default workflow

1. Inventory the source files. Keep the original files read-only.
2. Determine the output folder, usually a course/topic folder such as:

   ```text
   notes/<课程名>/
   ```

3. Extract:

   - PPT/PPTX -> `ppt-to-md-for-obsidian/scripts/ppt_to_obsidian_pipeline.py`
   - PDF/images -> `pdf-ocr-to-markdown/scripts/batch_ocr.py`

4. Rewrite raw extraction into editable notes, preserving source markers and slide/page boundaries.
5. For formula-heavy content, pass the draft through `math-physics-complete` before finalizing.
6. Leave the Markdown files in place for later small manual edits.
   - When the remaining work is bulk cleanup rather than writing (duplicate headings, link repair, navigation, vault-wide checks), hand off to `note-postprocess`.

## Output expectations

Prefer a structure the user can keep editing:

```text
notes/
  <课程名>/
    00_课程总览.md
    01_章节名.md
    知识点详细版_含公式.md
    知识点精简复习版_含公式.md
```

Every note should be self-contained enough to be edited later without the original script:

- keep `source` frontmatter pointing to the original file;
- write every section title as an ATX heading with the `#` prefix; level-2 sections must be `## 标题` (not bold text or setext underlines), so the Obsidian outline can locate them;
- keep `## Slide N` or page-boundary markers for traceability;
- preserve formulas as Obsidian-renderable math: `$...$` inline and `$$...$$` block (not `\(...\)` / `\[...\]`, which Obsidian shows as literal text);
- for old or inherited notes that still use `\(...\)` / `\[...\]`, convert them in bulk with `ppt-to-md-for-obsidian/scripts/fix_math_delimiters.py <notes-dir>` (`--check` / `--dry-run` available);
- mark missing or unclear content with `[待核实]` / `[unclear]` instead of guessing.

## Environment dependencies

The `NoteWorkplace` environment must contain a Python interpreter. Depending on which extraction path is used, it may also need:

```text
python-pptx>=1.0.2
PyYAML>=6.0
pypdf>=5.0.0
pdfplumber
PyMuPDF
Pillow
```

Install missing packages into `NoteWorkplace` only, for example:

```bash
conda run -n NoteWorkplace python -m pip install python-pptx pypdf pdfplumber PyMuPDF Pillow
```

Do not install packages globally or into `base` as part of normal workflow.
