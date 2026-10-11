---
name: pdf-ocr-to-markdown
description: OCR PDFs/images into user-facing Markdown with internal JSON/quality artifacts. Preserve originals; never create or modify PDFs. Use bundled scripts, not ad hoc OCR code. Mark visual pages pending unless images are actually inspected.
---

# PDF OCR to Markdown

Use this skill when the user provides PDFs or images and wants Codex to handle OCR, routing, visual-structure analysis, and final Markdown deliverables with internal JSON/quality diagnostics.

This skill does **not** modify the source PDF, add an OCR layer to the PDF, or create a searchable replacement PDF. "OCR" here means reading visible or embedded page content and writing separate structured outputs such as `final.md`, `final.json`, page-level Markdown, page-level JSON, and quality reports.

The default user experience should be: user gives files, Codex runs the bundled pipeline from the user's working directory, Codex decides the route per page, Codex performs the visual pass when it can actually inspect rendered images, and Codex reports the Markdown sidecar path next to each source file. Internal JSON, quality reports, and run artifacts stay under `.ocr_work/` so Finder-visible output is limited to the final Markdown sidecar unless the user asks otherwise.

## Runtime environment

This skill is part of the note-plugins collection. Run all bundled Python scripts with the `NoteWorkplace` conda environment interpreter, not system `python3` or `base`. The preferred interpreter is:

```text
/home/<你的用户名>/Programs/miniconda3/envs/NoteWorkplace/bin/python
```

If that binary is missing, do not silently fall back to system Python. Verify and, when necessary, install Python into the environment as described by the top-level `courseware-to-notes` skill. In command examples below, replace bare `python3` with the resolved `NoteWorkplace` Python.

## Quick Start

If the user asks Codex to OCR/analyze files, do not make them choose pipeline steps. From the user's current working directory, run the bundled script by absolute path:

```bash
python3 /path/to/pdf-ocr-to-markdown/scripts/batch_ocr.py file1.pdf file2.pdf image1.png
```

Dependencies should be installed once during skill installation with `scripts/setup_dependencies.py --install`. For Korean/English documents, the default language hint is already `kor+eng`. Only add options when the user asks for custom output locations, DPI, dependency repair, or work retention. The default internal output root is `.ocr_work/ocr_output`; the user-facing Markdown sidecar is copied next to the source file.

Dry-run for skill verification:

```bash
python scripts/batch_ocr.py \
  --file-list examples/example_input.md \
  --out-dir /tmp/ocr_output_test \
  --work-dir /tmp/ocr_work_test \
  --dry-run
```

Use `--keep-work` or `--no-cleanup` internally when pages need a surrounding Codex visual pass, because rendered page images and generated prompt files live under the work directory.

### Very large inputs (avoid `Payload Too Large`)

A 413 `Payload Too Large: Failed to buffer the request body` means a single request exceeded the API gateway buffer -- usually from loading a big PDF, merged Markdown, or many images into one turn. Do not fight it by retrying the same big read. For large sources, use the `payload-safe-batch` skill to split the work into small chunks that each run in a fresh session (`python3 skills/payload-safe-batch/scripts/plan_payload_split.py <files...>`), or keep the OCR pipeline's per-page artifacts on disk and read them a few at a time. The `pause-resume-checkpoint` hooks snapshot progress on `PreCompact` as an early warning.

## Agent Behavior

When a user provides files and says things like "OCR this", "analyze this PDF", "process these PDFs", or "extract everything":

1. Treat the file paths as the only required input.
2. Run from the user's current working directory, using the absolute path to this skill's script. Do not add `--auto-install-deps` during normal skill use; dependency setup belongs to installation.

   ```bash
   python3 /path/to/pdf-ocr-to-markdown/scripts/batch_ocr.py <files...> --keep-work
   ```

3. If the run fails because dependencies are missing, run `python3 /path/to/pdf-ocr-to-markdown/scripts/setup_dependencies.py --check` and explain that installation was incomplete. Only run `--install` as a repair step after user approval in the current environment. Do not silently install dependencies on every OCR request.
4. Open `.ocr_work/ocr_output/run_YYYYMMDD_HHMMSS/batch_summary.json` and each file's `metadata/route_report.json`.
5. If all pages are `text_only`, return the Markdown sidecar path next to each source file. The status is `complete`.
6. If any page is routed to `visual_focus`, `verify_text_and_visual`, or `full_vision_ocr`, inspect `metadata/prompt_queue.json` and `metadata/visual_pass_instructions.md`.
7. For each visual page, use Codex's local image-inspection capability on the image listed in `prompt_queue.json` — the small `.vision.jpg` **preview**, not the full-resolution `.png` (for example, `view_image` with the preview path). A full 240-DPI page PNG is several MB and base64-encoded it exceeds the API gateway's request-body buffer, producing HTTP 413 `Payload Too Large: Failed to buffer the request body`. If the preview is missing, regenerate previews (see below); if a page still 413s, process that single page in a fresh session. Read the matching generated prompt, inspect the image, and write raw page JSON to `expected_raw_json`. Do not check for or require `tesseract`; local OCR engines are not the visual pass. Write raw page JSON only after the rendered image has actually been inspected through an image-capable path. Never fill visual JSON from the extracted text, prompt text, filenames, or guesses alone.
8. After writing raw page JSON to the expected `raw_agent_outputs/.../page_000N.json`, merge and update the Markdown sidecar:

   ```bash
   python3 /path/to/pdf-ocr-to-markdown/scripts/merge_outputs.py \
     --run-out-dir .ocr_work/ocr_output/run_YYYYMMDD_HHMMSS \
     --work-run-dir .ocr_work/run_YYYYMMDD_HHMMSS
   ```

9. After final Markdown sidecars exist, ask the user in the same language they used whether to delete hidden work files. The question must make clear that `<safe_stem>_ocr.md` will remain, and hidden `.ocr_work/...` run artifacts, including internal JSON and quality reports, will be removed. Do not delete those work files until the user explicitly confirms.
10. If the user confirms cleanup, run:

   ```bash
   python3 /path/to/pdf-ocr-to-markdown/scripts/cleanup_workdir.py \
     .ocr_work/run_YYYYMMDD_HHMMSS \
     --out-run-dir .ocr_work/ocr_output/run_YYYYMMDD_HHMMSS \
     --delete-out-run
   ```

11. If the current runtime cannot attach images to a vision-capable model, be explicit: local extraction/rendering/routing is complete, but visual pages are pending. The status is `text_extraction_complete_visual_pending`, not `complete`, and you must not say "OCR complete".

The user should not need to understand `extract_pdf_text.py`, `render_pages.py`, `classify_pages.py`, or `merge_outputs.py` unless they ask for debugging or customization.

## Workflow

Run the batch script first unless the user asks for a specific lower-level step.

```text
input file list
-> dependency readiness check
-> run directory creation
-> file validation
-> PDF text extraction
-> PDF page rendering
-> page quality classification
-> route selection
-> page-level OCR / visual prompt generation
-> page-level output
-> file-level merge
-> batch-level report
-> cleanup
```

The main script:

1. Checks local dependency readiness. It installs missing Python packages only when `--auto-install-deps` is explicitly used as a repair or direct-script convenience.
2. Validates every path from positional inputs, `--files`, and/or `--file-list`.
3. Creates unique `.ocr_work/ocr_output/run_YYYYMMDD_HHMMSS/` and `.ocr_work/run_YYYYMMDD_HHMMSS/` directories.
4. Extracts embedded PDF text page-by-page with `pdfplumber` or `pypdf`.
5. Renders PDF pages with PyMuPDF into stable `page_0001.png` filenames.
6. Treats image files as one-page documents routed to `full_vision_ocr`.
7. Classifies each page with deterministic quality heuristics.
8. Generates page-specific prompts for all four routes.
9. Writes page Markdown/JSON artifacts, using extracted text for `text_only` and prompt-pending placeholders for visual routes unless raw visual output already exists.
10. Merges page artifacts into `final.md`, `final.json`, and `quality_report.md`.
11. Copies the user-facing sidecar deliverable next to each source file as `<safe_stem>_ocr.md`.
12. Writes `batch_summary.md` and `batch_summary.json`.
13. Cleans temporary work unless `--keep-work` or `--no-cleanup` is set. In normal agent use, ask before deleting hidden work artifacts after the Markdown sidecar is confirmed.

## Vision Honesty

The bundled Python scripts cannot attach rendered images to a vision-capable model by themselves. In normal skill use, Codex should run the local pipeline, then perform the visual pass itself if the surrounding runtime supports image-capable inputs. In Codex, that means using the available local-image inspection path on the small `.vision.jpg` page previews (each render writes them next to the full-resolution `.png`), not looking for `tesseract` or another local OCR binary. The user should only see the limitation if the current runtime truly cannot inspect local images.

### Image payload size (HTTP 413)

`Payload Too Large: Failed to buffer the request body` on an image view means the request body (the base64 image plus the conversation so far) exceeded the API gateway buffer. Two rules:

- Send the small `.vision.jpg` preview, never the full-resolution `.png`. Control the preview with `--vision-max-edge` (default 1568) and `--vision-quality` (default 80). A 240-DPI page PNG is often 1-5 MB; a 1568-px JPEG preview is usually a few hundred KB.
- Do not accumulate many viewed images in one session. Each viewed image stays in the request body. Process visual pages a few at a time, and when a long run starts failing, use `payload-safe-batch` to run one page per fresh session.

If previews are missing from an existing run, regenerate them without re-rendering by pointing `render_pages.py`'s preview helper at the rendered directory, or re-run `batch_ocr.py` with a lower `--dpi` (for example `--dpi 150`).

For pages routed to `visual_focus`, `verify_text_and_visual`, or `full_vision_ocr`, the raw visual result belongs at:

```text
.ocr_work/run_YYYYMMDD_HHMMSS/raw_agent_outputs/source_slug/page_0001.json
```

or:

```text
.ocr_work/run_YYYYMMDD_HHMMSS/raw_agent_outputs/source_slug/page_0001.md
```

Then refresh merges with the bundled script by absolute path:

```bash
python3 /path/to/pdf-ocr-to-markdown/scripts/merge_outputs.py \
  --run-out-dir .ocr_work/ocr_output/run_YYYYMMDD_HHMMSS \
  --work-run-dir .ocr_work/run_YYYYMMDD_HHMMSS
```

Prompt queue paths may be relative. Resolve them against the user's working directory where `batch_ocr.py` was run before opening images or writing raw outputs.

Raw visual JSON must match the planned `source_file`, `page`, and `route`; mismatches are rejected. Raw visual JSON cannot override trusted identity fields, and `content_blocks` must be a list of text strings or block objects with string fields.

Do not claim visual analysis is complete unless raw visual outputs have been supplied from an actual image inspection or a surrounding image-capable Codex session has analyzed the rendered page images. Do not write `content_blocks` with visual claims, `visual_annotations`, `tables`, `figures`, or `emphasis` from text-only context. Do not stop at `text_extraction_complete_visual_pending` merely because `tesseract` is unavailable.

## Routing Rules

Each page receives:

```json
{
  "page": 1,
  "text_quality": "good | medium | bad",
  "visual_density": "low | medium | high",
  "route": "text_only | visual_focus | verify_text_and_visual | full_vision_ocr",
  "reasons": [],
  "warnings": []
}
```

Use these route meanings:

- `text_only`: extracted PDF text is good and visual density is low. Normalize extracted text into ordered `content_blocks`. Do not run vision analysis or add visual claims.
- `visual_focus`: extracted PDF text is good and visual density is medium/high. Keep extracted text as the primary text source, but use the image to produce ordered `content_blocks` that preserve visible reading flow, including captions, figures, tables, equations, and footnotes.
- `verify_text_and_visual`: extracted text exists but is medium quality. Compare extracted text to the rendered page, correct obvious errors, preserve `[unclear]`, and produce ordered `content_blocks` from the visible reading flow.
- `full_vision_ocr`: extracted text is bad/empty, the page is scanned, `--force-full-vision` is set, or the source is an image. OCR visible text into ordered `content_blocks` and recover tables/figures/diagrams with uncertainty markers.

Default route mapping:

```text
good + low visual density     -> text_only
good + medium/high visual     -> visual_focus
medium text quality           -> verify_text_and_visual
bad text quality              -> full_vision_ocr
```

Quality heuristics include extracted text length, replacement/mojibake character ratio, excessive whitespace, one-character-per-line patterns, repeated garbage tokens, PDF image/vector object counts, table-like line density, suspiciously short text relative to page size, and image-file inputs.

## Outputs

Internal merge artifacts stay under the hidden output run directory:

```text
.ocr_work/
  ocr_output/
    run_YYYYMMDD_HHMMSS/
      batch_summary.md
      batch_summary.json
      files/
        source_file_id/
          final.md
          final.json
          quality_report.md
          pages/
            page_0001.md
            page_0001.json
          metadata/
            manifest.json
            route_report.json
            errors.json
            prompt_queue.json
            visual_pass_instructions.md
```

`source_file_id` is a safe directory name derived from the source filename without preserving the original extension as a folder extension. For example, `testfile.pdf` becomes `testfile_pdf`. The original filename is still recorded in `metadata/manifest.json` and page JSON.

The user-facing deliverable is also copied next to each source file. Dots and other non-extension separators inside the source stem become underscores so generated names do not look like stacked extensions.

```text
testfile.pdf
testfile_ocr.md
```

For example, `testfile.v1.pdf` writes `testfile_v1_ocr.md`. The corresponding JSON and quality report remain in `.ocr_work/ocr_output/run_YYYYMMDD_HHMMSS/files/.../`.

If two files in the same folder normalize to the same safe sidecar stem, later files get `_2`, `_3`, and so on before `_ocr`. For example, `a.b.png` and `a_b.png` write `a_b_ocr.md` and `a_b_2_ocr.md`.

Completion means the Markdown sidecar exists and the hidden batch summary records `completion_status`. If visual routes remain unanalyzed because no image-capable pass was possible, report `text_extraction_complete_visual_pending` rather than `complete`. After completion, ask whether to remove hidden work files; if confirmed, remove `.ocr_work/run_YYYYMMDD_HHMMSS/` and `.ocr_work/ocr_output/run_YYYYMMDD_HHMMSS/` while preserving the Markdown sidecar.

Temporary work stays under:

```text
.ocr_work/
  run_YYYYMMDD_HHMMSS/
    rendered_pages/
    extracted_text/
    generated_prompts/
    raw_agent_outputs/
    logs/
```

After a normal direct CLI run with no visual routes, the work run directory is removed unless `--keep-work` is set. In agent use, prefer `--keep-work`, finish any visual pass, then ask the user in their language whether to delete hidden work artifacts. If they confirm, use `cleanup_workdir.py --delete-out-run` so the completed task leaves only the Markdown sidecar visible. The cleanup helper refuses to delete paths outside `.ocr_work/run_*` and `.ocr_work/ocr_output/run_*` unless an explicit force repair path is used.

## Page Markdown Contract

Each page Markdown should read as a natural original-flow transcription, not a diagnostic report. Keep only a hidden page boundary comment, then emit the visible content in page reading order:

```md
<!-- page: 0001 -->

그림 1  피스톤을 앞뒤로 움직이면 종파가 관 내의 공기를 통해서 전달된다.

[Figure: 그림 1]
피스톤, 관, 공기 라벨, 압축 영역과 희박 영역이 보인다.

공기는 앞으로 밀려 다음 칸의 유체를 압축하고 압축의 펄스는 관을 따라 진행한다.

그림 2  펄스(압축대)가 유체로 채워진 관을 따라 속력 v로 오른쪽으로 진행한다.

[Figure: 그림 2]
관 내부에 압축대, 점 P/Q/R, 압력 p 및 p+Δp, 속도 v 및 v+Δv가 보인다.

210 | Part II  실험
```

Do not emit visible sections named `Extracted Text`, `Visual Annotations`, `Tables`, `Figures / Charts / Diagrams`, `Emphasis / Styling`, or `Warnings` in the final OCR Markdown. For `visual_focus`, body text must come from the PDF text layer, not whole-page re-OCR, but the image decides where blocks belong. For `full_vision_ocr`, text comes from actual image-based recognition and unreadable content must be `[unclear]`.

Do not repeat `source` or `route` comments on every page in user-facing Markdown. Source provenance belongs in the file header, JSON, and quality report. Route diagnostics belong in JSON and quality reports.

For multi-column pages, preserve coherent reading regions. Do not interleave left-column and right-column lines merely because their vertical positions are similar.

Figures and diagrams should be placed where they appear in the page reading flow. Describe only visible labels, captions, components, arrows, boxes, and unreadable areas. Do not interpret the underlying concept beyond what is visible.

Running headers, running footers, and page numbers are page furniture and do NOT need to be preserved. Omit them, or keep them only when they carry needed information; do not spend effort reproducing them. Keep footnote text that is real body content, and never drop real body text just to remove page furniture.

## JSON Contracts

Page JSON:

```json
{
  "source_file": "example.pdf",
  "page": 1,
  "route": "visual_focus",
  "text_quality": "good",
  "visual_density": "high",
  "content_blocks": [
    {
      "type": "paragraph",
      "text": "Visible paragraph text."
    },
    {
      "type": "figure",
      "label": "그림 1",
      "text": "Visible labels and neutral visual transcription.",
      "bbox": [0.05, 0.18, 0.95, 0.42]
    },
    {
      "type": "paragraph",
      "text": "Next visible paragraph text."
    }
  ],
  "text_markdown": "",
  "visual_annotations": [],
  "tables": [],
  "figures": [],
  "emphasis": [],
  "warnings": [],
  "uncertainties": []
}
```

### Figures become images

Figures should not stay as `[Figure: ...]` text. For any `figure | diagram | chart | image | table` block, include a `bbox`: the figure's region on the page image as normalized `[x0, y0, x1, y1]` in `[0, 1]` (origin top-left). When `merge_outputs.py` re-merges after the visual pass, it crops that region from the rendered page image and writes it next to the Markdown sidecar as `<safe_stem>_ocr_assets/fig_p<page>_<n>.png`, embedding it as `![label](./<safe_stem>_ocr_assets/fig_p0001_01.png)`.

Control it with `--figures`:

- `crop` (default): crop the `bbox`; if a figure block has no `bbox`, trim the page to its ink bounds (margins) so the fallback is not a full-size page.
- `page`: always embed the whole page image (useful when bboxes are unreliable).
- `placeholder`: keep the old `[Figure: ...]` text behavior.

Fallback and size controls:

- `--figure-trim` / `--no-figure-trim` (default on): with no `bbox`, drop near-white page margins before embedding. It is best-effort; full-bleed or noisy scans are left as-is.
- `--figure-max-edge` (default 1600): downscale the embedded image so its longest edge is at most this many px (`0` disables). Fallback pages are saved as JPEG, precise `bbox` crops as PNG.

Assets are written beside the sidecar, so the note stays portable: move the `.md` and its `_assets/` folder together.

`batch_ocr.py` renders figures in the same pass when raw page JSON already exists. Point it at a previous run with `--raw-from <dir>` (a work-run dir containing `raw_agent_outputs/`, or that directory itself), or use `--reuse-raw` to auto-pick the most recent previous run under `--work-dir`. It accepts the same `--figures` / `--figure-trim` / `--figure-max-edge` options. Without either flag, figures are produced by the `merge_outputs.py` step after the visual pass, which is the normal flow.

`content_blocks` is the primary final-Markdown source. The other visual fields are optional metadata and fallback compatibility fields; they must not drive the user-facing Markdown when `content_blocks` exists.

Batch summary JSON:

```json
{
  "run_id": "",
  "input_files": [],
  "processed_files": [],
  "failed_files": [],
  "total_pages": 0,
  "routes": {
    "text_only": 0,
    "visual_focus": 0,
    "verify_text_and_visual": 0,
    "full_vision_ocr": 0
  },
  "outputs": [],
  "warnings": [],
  "errors": []
}
```

Every successfully processed file must produce `final.md`, `final.json`, and `quality_report.md`. Missing files, unsupported files, dependency failures, and failed pages must be recorded in `errors.json` and `batch_summary.md`/`.json`.

## Progress Metrics

The main script prints and writes measurable checkpoints:

```text
[1/9] Input validation complete: N files found, M missing/invalid
[2/9] Run directory created
[3/9] PDF text extraction complete: X/Y pages extracted
[4/9] Page rendering complete: X/Y pages rendered
[5/9] Page classification complete: text_only=A, visual_focus=B, verify_text_and_visual=C, full_vision_ocr=D
[6/9] Page-level prompts generated: X/Y pages
[7/9] Page-level outputs collected/generated: X/Y pages
[8/9] File-level merge complete: X/Y files
[9/9] Cleanup complete: removed Z temporary files, kept K final artifacts
```

## Dependencies

Preferred local dependencies:

- Python 3
- `pdfplumber` or `pypdf` for text extraction
- `pymupdf` for rendering PDFs
- `Pillow` for image metadata

Dependencies should be installed once when the skill is installed, not during every OCR request. If dependencies are missing, scripts should fail with installation guidance rather than obscure tracebacks. Avoid network access unless the user explicitly allows dependency installation.

Setup check only:

```bash
python3 scripts/setup_dependencies.py --check
```

Setup install:

```bash
python3 scripts/setup_dependencies.py --install
```

In normal skill use, Codex should call `batch_ocr.py` without `--auto-install-deps`. Reserve `--auto-install-deps` for explicit repair, direct-script convenience, or a user-approved recovery path after `setup_dependencies.py --check` reports missing packages.

## Bundled Scripts

- `scripts/batch_ocr.py`: full batch coordinator and dry-run entrypoint. It writes a Markdown sidecar plus internal JSON outputs and never rewrites the source PDF.
- `scripts/setup_dependencies.py`: checks and installs required local Python dependencies.
- `scripts/extract_pdf_text.py`: standalone PDF text-layer extractor.
- `scripts/render_pages.py`: standalone PDF renderer and image stager.
- `scripts/classify_pages.py`: deterministic page classifier.
- `scripts/merge_outputs.py`: file merge helper and raw visual-output refresh tool.
- `scripts/cleanup_workdir.py`: cleanup helper.

Prompt templates are in `prompts/`. Example input and expected output layout are in `examples/`.
