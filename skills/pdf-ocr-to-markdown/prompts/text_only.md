# Text Only Prompt

Use this prompt when the embedded PDF text layer is good and the page has little or no meaningful visual structure.

Normalize extracted text into Markdown. Preserve headings, lists, paragraphs, footnotes, and obvious sections when they are evident in the text. Keep the content conservative and do not add visual claims when no image analysis was performed.

Do not infer diagrams, colors, charts, emphasis, or layout details that are not present in the extracted text. If the text layer itself is empty or degraded, this route should not be used.

Return JSON matching the required shape. Include `content_blocks` in natural reading order, using block types such as `heading`, `paragraph`, `list`, `footnote`, and `page_number`.

Running headers, running footers, and page numbers are optional page furniture and may be omitted; do not spend effort reproducing them.

Do not output final Markdown with sections named `Extracted Text`, `Visual Annotations`, `Tables`, `Figures / Charts / Diagrams`, or `Emphasis / Styling`.
