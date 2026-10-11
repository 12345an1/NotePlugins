# Full Vision OCR Prompt

Use this prompt when the page is scanned, the PDF text layer is empty or poor, or the source is an image file.

OCR all visible text from the rendered page image. Preserve reading order as well as the page layout allows.

Return the page as ordered `content_blocks`, not as a diagnostic report. The final Markdown should read like the original page flow:

- text block
- figure/table/equation block in the position where it appears
- next text block
- footnote/footer block where appropriate

For multi-column pages, keep coherent reading regions together. Do not alternate left-column and right-column lines just because OCR sees them near the same vertical position.

Use this block shape:

```json
{
  "type": "heading | paragraph | list | caption | figure | table | equation | footnote | page_number | unclear_block",
  "text": "visible text or neutral visual transcription",
  "markdown": "optional Markdown when text alone is not enough",
  "label": "optional visible label such as 그림 1",
  "level": 2,
  "bbox": [0.05, 0.18, 0.95, 0.42]
}
```

For a `figure | diagram | chart | image | table` block, also return `bbox`: the region the figure occupies on the page image, as normalized `[x0, y0, x1, y1]` in `[0, 1]` with the origin at the top-left. The merge step crops this region out of the rendered page and embeds it as a real image in the note, so a bbox that is too large (e.g. the whole page) or missing falls back to embedding the whole page or the `[Figure: ...]` placeholder. Omit `bbox` for text blocks.

Recover visible structure:

- headings, paragraphs, lists, footnotes, and captions
- tables as Markdown tables where possible
- charts, figures, diagrams, screenshots, labels, colors, emphasis, annotations, and relationships
- arrows, connectors, callouts, boxed text, and highlighted regions

For figures and diagrams, transcribe visible labels, captions, and components neutrally. Do not explain the underlying concept beyond what is visible. Place the figure block where it appears in the page reading flow.

Running headers, running footers, and page numbers are page furniture and do NOT need to be preserved. Omit them, or keep them only when they carry needed information. Keep footnote text that is real body content.

Mark unreadable text as `[unclear]`. Do not hallucinate numbers, labels, captions, axis values, legends, footnotes, or small print. If a table or chart is partly readable, include only the readable values and mark unclear cells explicitly.

Return JSON matching the required shape. `content_blocks` is required and is the primary final-Markdown source. Keep legacy fields such as `visual_annotations`, `tables`, and `figures` only as optional metadata.

Do not output final Markdown with sections named `Extracted Text`, `Visual Annotations`, `Tables`, `Figures / Charts / Diagrams`, or `Emphasis / Styling`.

Only produce this output after the rendered page image or source image has actually been inspected. Do not OCR from filenames, prompt context, or extracted text placeholders.
