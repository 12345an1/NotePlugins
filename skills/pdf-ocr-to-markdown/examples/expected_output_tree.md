# Expected Output Tree

```text
.ocr_work/
  ocr_output/
    run_YYYYMMDD_HHMMSS/
      batch_summary.md
      batch_summary.json
      files/
        sample_text_pdf/
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

The user-facing Markdown sidecar is copied next to the source:

```text
examples/
  sample_text.pdf
  sample_text_ocr.md
```

Temporary files are written under `.ocr_work/run_YYYYMMDD_HHMMSS/`. Internal merge artifacts, including `final.json` and `quality_report.md`, are written under `.ocr_work/ocr_output/run_YYYYMMDD_HHMMSS/`. In agent use, ask the user before deleting hidden work artifacts.

Minimal invocation:

```bash
python scripts/batch_ocr.py examples/sample_text.pdf --auto-install-deps
```
