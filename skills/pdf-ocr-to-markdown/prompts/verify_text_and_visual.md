# Verify Text And Visual Prompt

Use this prompt when extracted PDF text exists but appears unreliable.

Compare the extracted text with the rendered page image. Correct obvious extraction errors, broken characters, missing spaces, ordering issues, and table/list structure when the visible page makes the correction clear.

Return the page as ordered `content_blocks`, not as separated analysis buckets. The final Markdown should follow the visible page reading flow. For multi-column pages, keep coherent reading regions together instead of alternating lines across columns.

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

Preserve uncertainty:

- write `[unclear]` for unreadable text
- do not fabricate values, labels, captions, or footnotes
- record uncertainty and confidence notes in warnings or uncertainties

Also analyze visual elements: layout, colors, emphasis, highlights, figures, diagrams, charts, tables, arrows, callouts, captions, and relationships between text and visuals.

For figures and diagrams, transcribe visible labels, captions, and components neutrally. Do not explain the underlying concept beyond what is visible. Place the figure block where it appears in the page reading flow.

Running headers, running footers, and page numbers are page furniture and do NOT need to be preserved. Omit them, or keep them only when they carry needed information. Keep footnote text that is real body content.

Return JSON matching the required shape. `content_blocks` is required and is the primary final-Markdown source. Keep legacy fields such as `visual_annotations`, `tables`, and `figures` only as optional metadata. Include confidence and warnings for corrections that are plausible but not certain.

Do not output final Markdown with sections named `Extracted Text`, `Visual Annotations`, `Tables`, `Figures / Charts / Diagrams`, or `Emphasis / Styling`.

Only produce visual corrections or visual-structure claims after the rendered page image has actually been inspected. Do not fill visual fields from extracted text alone.
