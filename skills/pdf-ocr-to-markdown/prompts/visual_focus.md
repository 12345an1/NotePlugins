# Visual Focus Prompt

Use this prompt when the PDF text layer is good and the page has meaningful visual structure.

The extracted text is the primary text source. Do not OCR the entire body text again. Use the rendered page image only to recover visual information that the text layer does not express.

Return the page as ordered `content_blocks`, not as separated analysis buckets. The final Markdown should follow the visible page reading flow. Use the extracted text for body text, but use the rendered page to decide where paragraphs, captions, figures, tables, equations, and footnotes belong.

For multi-column pages, keep coherent reading regions together instead of alternating lines across columns.

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

Focus on:

- layout hierarchy and reading order clues
- colors, highlights, emphasis, underlines, and boxed text
- figures, diagrams, screenshots, charts, tables, and captions
- arrows, callouts, labels, connectors, and relationships between elements
- visible mismatch between the rendered page and extracted text

Report possible text-layer mismatches, missing labels, clipped text, or suspicious ordering. Do not invent unreadable numbers, labels, captions, footnotes, or values. Use `[unclear]` for content that cannot be read.

For figures and diagrams, transcribe visible labels, captions, and components neutrally. Do not explain the underlying concept beyond what is visible. Place the figure block where it appears in the page reading flow.

Running headers, running footers, and page numbers are page furniture and do NOT need to be preserved. Omit them, or keep them only when they carry needed information. Keep footnote text that is real body content.

Return JSON matching the required shape. `content_blocks` is required and is the primary final-Markdown source. Keep body text from the extracted text layer unless a visible mismatch must be reported. Keep legacy fields such as `visual_annotations`, `tables`, and `figures` only as optional metadata.

Do not output final Markdown with sections named `Extracted Text`, `Visual Annotations`, `Tables`, `Figures / Charts / Diagrams`, or `Emphasis / Styling`.

Only produce this output after the rendered page image has actually been inspected. Do not infer visual hierarchy, tables, figures, colors, emphasis, labels, or relationships from extracted text alone.
