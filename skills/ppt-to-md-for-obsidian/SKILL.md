---
name: ppt-to-md-for-obsidian
description: Use when the starting source is local PPT/PPTX/PDF lecture courseware or slide-derived files and the user wants extracted text, Obsidian Markdown course notes, source coverage, formulas, examples, or review pages; Chinese triggers include PPT转笔记, 课件转Markdown, PDF课件整理. Once notes are drafted and only cleanup remains, use $note-postprocess instead.
---

# PPT To Markdown For Obsidian

## Runtime environment (note-plugins override)

This installed copy is part of the `note-plugins` collection. Run every bundled Python script with the `NoteWorkplace` conda environment, not system `python3` or `base`.

Preferred interpreter:

```text
/home/<你的用户名>/Programs/miniconda3/envs/NoteWorkplace/bin/python
```

If that binary is missing, verify that `conda run` resolves to the same environment before running anything; do not silently fall back to `/usr/bin/python`. The top-level `courseware-to-notes` skill contains the required check. In all command examples below, replace bare `python3` with the resolved `NoteWorkplace` Python.

## Goal
Convert slide-based course material into an Obsidian note system, not a raw slide transcript. The output should be readable as standalone study notes with concept explanation, mechanisms, formulas, examples, boundaries, and useful cross-links.

When writing into an existing project or vault, first load project-local guidance such as `AGENT.md`, `agent.md`, and files under `agent/`. Treat project-local guidance and scripts as the source of truth when they are stricter or more current than this skill. Keep source files read-only unless the user explicitly asks to rename, move, or delete them. For vault-only organization work, use `$note-postprocess`.

When updating this skill itself, edit only the source skill repository, never the installed copy under `~/.codex/plugins/cache/`. Validate the bundle from the skill root with `python3 scripts/validate_skill_repo.py`, then refresh the installed copy through the plugin marketplace (`codex plugin add note-plugins@personal`) only when the user explicitly authorizes it.

When a target vault follows solvenotes-style conventions, read `references/solvenotes-profile.md` before validation or source-evidence work. Solvenotes keeps self-contained `source_manifest.md` files outside Notes, under `SOLVENOTES_MANIFEST_ROOT` or `Notes.parent / "vault_sources"` by default, mirroring each course/topic path relative to Notes. Reject a manifest root inside Notes, including symlink resolutions. Keep private mappings out of the public Skills repository. Preserve `source_files`, source URLs, and page/slide markers in notes. Missing required manifests are FAIL/unavailable, never an empty PASS. Solvenotes also forbids `99_内容覆盖审查.md` and `coverage_audit` pages; other vaults retain their local provenance layout.

## Handoff Boundaries

Use this skill while source extraction, slide-order reconstruction, or the first full rewrite of the notes is still part of the task. Once the notes have been drafted and the remaining work is only duplicate-heading cleanup, link repair, navigation restructuring, or vault-wide validation, switch to `$note-postprocess`.

If the source is a public URL or web page instead of a local file, obtain a permitted local copy of the courseware first; this skill works from local PPT/PPTX/PDF files.

## Quick Start

1. Resolve source files, target note folder, local guidance, and conversion mode.
2. Extract or inventory the source with the bundled scripts before rewriting.
3. Build a source-to-note coverage map for slides/pages, formulas, examples, and exam-scope terms.
4. Write or update notes only after coverage gaps and assumptions are explicit.
5. Run project-local link, note-quality, residue, example, and coverage checks before the final response; use bundled checks only when the project has no equivalent.

## Evidence And Assumption Gate

Treat slide/page text, visible titles, formulas, filenames, course order, and user-provided scope as evidence. Mark anything inferred from noisy extraction as an assumption, and do not present it as source coverage unless it has been checked against an extracted or visible source. For missing or unreadable source sections, report the gap and its impact on the output instead of silently filling it.

For paper sources, verify the full title, year, and venue or publication status from the paper title page or an official bibliographic record. Do not infer them from a filename, surrounding notes, or memory. Distinguish a dated arXiv preprint from a peer-reviewed venue when the source only supports the former.

## Source Ownership And Repair Protocol

- In frontmatter, keep the note's own primary source explicit. Add another PDF/PPTX only when the note body explicitly cites, compares, or uses that source; do not build `source_files` or `source_manifest.md` by keyword similarity alone.
- When chapter numbering or source names have shifted, repair the existing frontmatter and manifest target rather than adding a second mapping, audit paragraph, or reviewer note. A manifest row must point to the note that owns the source's subject.
- Separate source fact, course-era convention, empirical observation, and generated teaching example. Put the scope beside the claim: dataset, metric, hardware, market/board, clinical population, date, software version, or tokenizer version.
- Replace unsupported absolutes (`首次`, `完全解决`, `必然`, `保证`, `最强`) with the narrowest source-backed statement. Preserve a priority claim only when the cited paper or official record supports it at a locatable passage.
- Treat high-risk domains as date/versioned content. Verify medical thresholds and medication routes, financial settlement and trading rules, and API/DBMS behavior against an authoritative source before presenting them as current.

## Semantic Correctness Gate

Treat formulas, definitions, algorithms, examples, and paper conclusions as claims that need semantic review, not just Markdown or LaTeX syntax checks.

- For every nontrivial formula, define symbols, domains, signs, shapes, and boundary conditions next to it. Check dimensions, limiting cases, and whether a floor/ceiling or divisibility condition is part of the convention. Distinguish a Lipschitz function from a Lipschitz gradient, a necessary condition from a sufficient condition, and a model assumption from an empirical observation.
- For definitions, state the scope: API/DBMS version, hardware model, course convention, market/board, or other operating assumptions. Replace universal wording with the narrowest statement supported by the source.
- For algorithms and proofs, make the construction, invariant, answer mapping, stopping condition, and failure case explicit. Check that every worked example's answer actually matches its question; use symbolic calculation or randomized tests when a deterministic check is practical.
- For current rules and paper results, record the applicable object, date/version, metric, dataset, and source. A reported table or ablation supports a scoped experimental claim; it is not automatically a theorem, necessity claim, or first-in-field claim.
- When a semantic repair affects a repeated concept, update the chapter note and the detailed/concise review copies together, or state why a copy is intentionally scoped differently.
- For computability reductions, write the reduction direction and any complement explicitly, then verify both cases of the constructed machine. Do not replace a proof obligation with a memorized problem-name pairing.
- For condition-bearing theory such as A*, KKT, Slater, complexity bounds, or universal approximation, put the search mode, sign convention, constraint qualification, domain, activation, or asymptotic assumptions next to the conclusion. Separate necessity, sufficiency, strong duality, and empirical evidence.
- For medicine, finance, and other high-stakes notes, state units, population/product/market, and applicable date. Verify diagnostic thresholds, medication examples, fees, taxes, settlement/trading rules, and board-specific rules against an authoritative source; if the source is course-era or incomplete, label it as historical/course scope instead of guessing a current rule.

## Audit Output Placement

Write corrected explanations and traceable source markers into the notes. Keep temporary issue ledgers, machine-readable audit data, intermediate reports, and reviewer-facing prose outside the vault by default. Create or update an in-vault coverage page only when the user explicitly requests it or local guidance or a validator requires the named artifact. Do not recreate a removed quality directory merely to hold audit output.

For Solvenotes, local guidance is definitive: update the applicable self-contained `source_manifest.md` in the external manifest store; never place it in Notes. Do not create or update `99_内容覆盖审查.md` or a `coverage_audit` note. Record blank-text, OCR, visual-only, extraction, mapping, and semantic-verification limits honestly in the manifest, without treating aggregate ranges as per-unit semantic proof.

Resolve `TODO`, `待核实`, and other placeholders before delivery. If evidence remains unavailable, state its bounded source status directly in the note. Replace review labels such as `不能泛化`, `这不是对所有任务的保证`, and `证据边界` with the supported dataset, metric, model configuration, market, date, or clinical setting.

## Default Outputs

For each course or topic directory, prefer this structure:

- `00_课程总览.md`, `00_学习地图.md`, or a local `00_*总览.md` variant as the navigation entry.
- Numbered chapter notes such as `01_绪论.md`, `02_知识表示.md`.
- `知识点详细版_含公式.md` as the full review page.
- `知识点精简复习版_含公式.md` as the fast review page.
- A self-contained `source_manifest.md` when multiple source files are involved or extraction order could be disputed. For Solvenotes, this is an external provenance deliverable, never a file in the course/topic note directory.
- For non-Solvenotes vaults only, an existing `99_内容覆盖审查.md` when local guidance or a validator requires that legacy artifact, following Audit Output Placement.

Keep the detailed review and concise review as two separate files. Do not replace the detailed version with the concise version. When an existing vault uses course-prefixed review pages, preserve that convention, for example `游戏数值策划知识点详细版_含公式.md` and `游戏数值策划知识点精简复习版_含公式.md`.
If the user explicitly asks for one exam review file instead of two review pages, keep the single file but make the overview link it and validate with `--allow-exam-review`.

## Workflow

1. Inventory the source files and existing notes first.
   - Identify PPT/PPTX/PDF/DOCX source files, their course order, and whether matching notes already exist.
   - Treat source files as read-only unless the user explicitly asks to rename, move, or delete them.

2. Extract slide text while preserving slide order.
   - For `.pptx`, use `scripts/extract_pptx_text.py` when a deterministic text dump helps. A successful `python-pptx` backend does not prove visual coverage: blank-text slides and media objects still require visual inspection.
   - For legacy `.ppt`, use `scripts/convert_ppt_to_pptx.py` to convert with LibreOffice first, then extract. If LibreOffice is unavailable or conversion fails, use the bundled `scripts/extract_legacy_ppt_text.py` OLE/CFB text-record fallback, count text records, and mark fallback or 0-record files as partial text/OCR-limited extraction rather than claimed full coverage. The pipeline performs this fallback automatically and records it in `pipeline_manifest.md`.
   - For `.pdf` courseware, use `scripts/extract_pdf_text.py`; it reads one stable input snapshot, tries `pypdf`, `pdfplumber`, and `pdftotext`, and continues when a backend returns all-empty pages or very low text coverage. The helper rejects inputs above 256 MiB, more than 10,000 pages, or more than 64 Mi characters of extracted text; the external `pdftotext` call also has a 60-second timeout. Preserve the reported backend, page count, empty-page count, and low-coverage warning in coverage notes when relevant. If the final backend still reports low text coverage, treat the PDF as likely scanned/image-only until OCR or manual inspection confirms coverage; the in-process Python parsers have size budgets but are not a separate OCR or visual pass.
   - If the extracted text contains formula noise, run `scripts/clean_latex_from_ppt.py` before rewriting.
   - If existing notes (yours or inherited) use legacy math delimiters `\(...\)` / `\[...\]`, run `scripts/fix_math_delimiters.py <notes-dir>` to rewrite them to the Obsidian-renderable `$...$` / `$$...$$` form. It also expands inline/crammed `$$...$$` onto their own lines (`--normalize-blocks`, on by default; use `--no-normalize-blocks` to keep them). It never touches fenced code blocks or inline code spans, and `--check`/`--dry-run` report without writing.
   - For repeatable runs, use `scripts/ppt_to_obsidian_pipeline.py` to extract, clean, and write a manifest. The pipeline keeps a basename when it is unique and adds a stable source-path hash when same-named files would otherwise overwrite one another. Run extraction and its `pipeline_manifest.md` in external staging; they are intermediate artifacts, not the formal Solvenotes manifest or learning notes.
   - If extraction is noisy, use slide titles, visible bullets, formulas, filenames, and course order together instead of trusting raw text blindly.
   - If attempting visual validation, verify that rendered images/contact sheets cover every page or slide. A QuickLook thumbnail that produces one image for a multi-slide PPTX is only a cover preview, not page-level visual evidence.

3. Do a source coverage pass before writing the final notes.
   - Build a source-to-output map by file, chapter, slide/page range, and major headings.
   - Pull out formulas, algorithms, examples, derivation steps, definitions, assumptions, and warnings from each source file.
   - Every source-derived example must carry a traceable marker such as `（/课程/文件或章节 p.N）`. If PPT/PDF text extraction has no standalone example, add an auxiliary question and say naturally that it is a self-written teaching example rather than a source exercise. A compact table cell may use `自拟教学例；背景见 课程/文件.pptx slide N` without explaining differences from the original page. The older label `自拟教学例：源课件未提供可独立还原的对应例题` remains accepted for compatibility; it is optional.
   - Every example must include a detailed explanation, not just an answer. Include the tested concept, known conditions, formula or rule choice, substitution or reasoning steps, conclusion, and the common mistake or boundary condition to avoid.
   - Write auxiliary questions in natural prose while keeping the givens, rule or formula, reasoning steps, conclusion, common pitfall, and provenance easy to find. Use labels such as `题目`, `解法`, `关键陷阱`, and `来源说明` only when they improve readability, not as repeated boilerplate.
   - Keep source-derived examples and generated auxiliary questions visibly distinct. Never relabel a generated question as a PPT/PDF example. If examples are graded or reviewed by project scripts, improve low-grade rows by adding analysis, steps, conclusion, and pitfalls while preserving the original source marker.
   - Resolve `需复核`, `人工确认`, and `open the slides manually` states before delivery. If evidence remains unavailable, use the bounded source-status rule under Audit Output Placement. A non-Solvenotes vault may update a locally required legacy audit artifact; Solvenotes records the formal coverage boundary in its external `source_manifest.md` while preserving claim-specific source scope in notes.
   - If extracted PDF/OCR cells are obvious noise, mark them as extraction noise while preserving file/page mapping. Do not turn garbled strings into invented topics, examples, or formulas.
   - For exam-review requests, treat the exam outline or teacher-provided scope as a first-class source alongside PPT/PDF files. Preserve exact outline terms and common compact/space variants in the coverage map, for example `CPU性能公式` and `CPU 性能公式`.
   - Compare the source map with the requested exam scope. Mark topics as `included`, `out of scope by user`, `source noisy`, or `missing`.
   - If the material is long or the user asks for strict checking, write or update a self-contained `source_manifest.md`. Follow Audit Output Placement for temporary audit output; Solvenotes stores the manifest externally and has no in-vault coverage page.
   - Keep coverage statuses internally consistent. Distinguish image-only/unreadable visual pages from real content gaps, and do not describe a single unresolved example as the only uncertainty when separate visual pages still need confirmation.
   - Do not claim completion from a short outline. If the notes do not yet explain the mechanisms, formulas, and examples from the source, keep expanding them.
   - If a keyword sweep reports a missing topic, verify whether the miss is caused by wording variation before treating it as absent; then add the source wording near the relevant concept or document why it is out of scope.

4. Rewrite into primary notes.
   - Use Chinese as the main language; keep standard English terms such as `Transformer`, `BERT`, `CLIP`, `SVM`.
   - Use ATX headings everywhere. The note title is one `# 标题`, every level-2 section must be written as `## 标题` (a `##` prefix followed by a space), and deeper sections use `###`/`####`. Never use bold text, plain lines, or setext underlines (`===` / `---`) as section titles: Obsidian's outline and heading anchors only locate real `#`-prefixed headings.
   - Convert bullet fragments into continuous explanations.
   - Add variables and assumptions directly after formulas.
   - Use `$$ ... $$` for block formulas.
   - Write inline math with `$...$` and block math with `$$...$$`. Do NOT use `\(...\)` or `\[...\]`: Obsidian renders those as literal text, not formulas. For old notes that still use them, run `scripts/fix_math_delimiters.py <notes-dir>`.
   - For exam material, include definitions, formula meanings, derivation logic, calculation examples, decision rules, common traps, and boundary conditions.
   - If the user asks for `（简答）` marks, place the mark on the local heading or bullet where the answer should be memorized; do not create a detached list of brief-answer topics.
   - Write worked examples as followable calculations: known conditions, formula choice, substitution, intermediate value, conclusion, and the common mistake to avoid. Before delivery, re-evaluate the answer against the question and record the acceptance/stop condition. When the source only gives the final answer, reconstruct the missing steps from the surrounding formula and state any assumption.
   - For zero-base standalone review files, do not write `see PPT`, `as above`, or source-dependent shortcuts. Include the definition, formula variables, decision rule, and example steps in the file itself.
   - For probability/statistics, write the likelihood, posterior, risk, estimator bias, or gradient formula before explaining it in words.
   - For algorithms, include the update rule, stopping condition, convergence intuition, and at least one failure case when relevant.
   - For algorithm-heavy notes, include pseudocode or a structured procedure with input, loop/recurrence, update, stopping condition, and output.
   - For complex calculation examples, include intermediate values or a small step table, especially for probability, optimization, numerical planning, and AI algorithm questions.
   - For paper notes, add a compact `## 论文信息` section near the title with the full paper title, year, venue or explicit preprint status, and a traceable source link/file. Then cover problem gap, core method, experiment conclusion, failure boundary, and reproducibility notes. If the project has a paper-note review checklist, use it to drive revisions.
   - Treat priority and universal-success language such as `首次`, `第一种`, `完全解决`, and `保证优于` as evidence-bearing claims. Keep it only when the paper or an official record supports the claim at a locatable passage; otherwise attribute the narrower claim to the authors or rewrite it as a scoped experimental result.
   - For paper notes in Chinese vaults, prefer explicit sections named `## 可复现要点` and `## 失败边界` when the local checklist expects them. State the paper-specific reproduction obstacle—such as an unavailable training split, missing checkpoint, unstated preprocessing, absent evaluation script, or unpinned dependency—instead of repeating a generic code/checkpoint disclaimer; do not invent availability.
   - Avoid generic study plans, empty templates, and repeated bridge sentences.
   - Avoid headings or filler such as `例题模板`, `高频答题模板`, `套话`, `空话`, or placeholder-like wording. Write the actual question-solving rule instead.
   - Reduce repeated contrast frames such as `不是...而是...`; use direct definitions, conditions, and consequences instead.

5. Build Obsidian navigation.
   - Add or update the course overview.
   - Link concepts where they first become relevant.
   - For cross-course concept pages, build reciprocal navigation when practical: concept pages link to concrete chapters, and chapter notes link back to the concept page at the first relevant occurrence.
   - Avoid dumping large link lists at the end of every note.
   - Use wiki links such as `[[课程目录/文件名|显示文本]]`.
   - Inside Markdown tables, avoid wiki-link aliases such as `[[课程目录/文件名|显示文本]]` because the alias pipe can split the table cell. Prefer `[[课程目录/文件名]]` in tables, or put the alias link outside the table.
   - Escape literal pipes in table cells, including regular expressions such as `` `a\|b` ``.

6. Add review pages.
   - The detailed version should retain the full course mechanism and formulas.
   - The concise version should keep the main chain, core formulas, and common mistakes.
   - If the user says a long review page must not be split, keep it as one file.
   - A single exam review page still needs full derivations, examples, and comparison tables when those are in scope. It should not collapse into a formula list.

7. Validate before finishing.
   - Prefer project-local validators from `AGENT.md` when present. If the project follows a known profile such as solvenotes, load its bundled reference before choosing checks.
   - If the project has no equivalent validators, check broken links and self-links with `scripts/check_obsidian_links.py`.
   - If the project has no equivalent validators, check course-note output structure with `scripts/check_course_notes.py`; it also catches unbalanced fences/math, stale residue, and malformed Markdown tables.
   - For strict PPT/PDF coverage audits, run the project-local source coverage checker first. If only the bundled `scripts/check_source_coverage.py` is available, run it with explicit source-to-notes mappings.
   - Treat the selected source and notes roots as trust boundaries: reject absolute paths, `../` escapes, and mappings that resolve outside those roots. A local paper-note `source_files` entry or body source reference must use the exact root-relative spelling of an existing regular file; reject directory targets and every leaf or ancestor symlink, including broken links. Provide complete mappings for renamed directories, and keep standalone systems such as `概念索引`, `模板`, `游戏数值策划`, `科研方法论`, `算法岗学习笔记`, and `学习路径` outside course reconciliation.
   - The course-note checker accepts either exact review filenames or local course-prefixed review filenames; do not rename working review pages only to satisfy a generic template.
   - For a generic vault whose local contract requires a separate coverage audit, run `scripts/check_course_notes.py --strict-depth --require-coverage-audit`; add `--allow-exam-review` when using one exam review file instead of the two default review pages. Never pass `--require-coverage-audit` for Solvenotes; use its external maintainer coverage checker and strict source-file checker with the resolved manifest root.
   - If a vault contains non-course generated index or audit folders, run this checker per course directory or pass repeated `--skip-dir <name>` values for directories such as `概念索引`. Report the thresholds and skipped directory names used.
   - Check empty files, conflict markers, leftover template phrases such as `相关知识链接`, and review-page coverage.
   - Run a direct keyword/formula sweep against source-derived terms before the final response. Missing hits should be explained as out of scope, noisy extraction, or corrected before delivery.
   - For paper-note work, also check that each actual paper note records title, year, venue/status, source evidence, contribution, and failure boundary. Read strong-claim keyword hits in context; negated boundary statements are not errors.
   - Add targeted semantic regression checks for every repaired formula, example, or scope-sensitive claim (for example, evaluate the worked assignment, verify a construction's two cases, or assert that the corrected floor/sign/condition is present). If Ruff is unavailable, activate an existing project environment or install it only when the package is within the user's stated size budget; report that environment choice.
   - After repairs, sweep all synchronized chapter, detailed-review, concise-review, and summary files for the old claim and the new scoped wording. Check that no reviewer-language residue, stale numeric rule, or unmarked generated example remains.
   - Keep source-fidelity status separate from note-quality status. If `SOLVENOTES_SOURCE_ROOT` or another source root is unavailable, report the strict source check as blocked rather than treating a passing local note check as source verification.
   - When the user requests multiple strict check rounds, make them distinct: file quality, outline/source coverage, and vault/navigation checks. Rerun affected checks after the last edit.
   - For residue scans, read each hit in context before deleting it. Avoid false positives such as `指令系统的使用方法` being treated as generic `使用方法` filler.
   - When writing a validation report, state that the residue scan passed without repeating blocked marker words in the report itself; otherwise the report can create the next residue hit.
   - Before committing or uploading, run Git status in the target repository, stage only the intended files, and report unrelated dirty files without modifying them.

## Conversion Modes

Choose one mode before rewriting:

- Course notes: numbered chapter notes, course overview, detailed and concise review pages.
- Research group presentation: problem, method, experiment, limitation, and discussion.
- Exam review material: concept map, formula table, typical question patterns, and common mistakes.

See `references/modes.md` when the mode is ambiguous or the user asks for a specific output style.

## Configuration

Use `skill-config.example.yaml` as a starting point for repeatable conversions. It defines the source directory, output directory, mode, cleanup settings, LibreOffice conversion path, and Obsidian naming preferences.

## Quality Bar

Good notes should answer:

- What problem does this slide/topic solve?
- What are the core concepts and how do they relate?
- Which formula matters, and what does each variable mean here?
- What assumptions or failure cases should the learner remember?
- Which nearby notes should be linked for cross-course recall?
- Which source pages/slides supplied the topic, and where did the final note cover it?

Poor notes usually look like:

- direct slide dumps or long bullet lists without explanation,
- formulas without variable meaning or duplicated link blocks,
- course overviews that become too long to navigate,
- exam review pages that contain only generic answer patterns instead of course-specific derivations, examples, and decisions.

## Output Contract

The final response should include:

- output folder, main note files created or updated, and source coverage by PPT/PPTX/PDF file, including extraction gaps,
- detailed/concise review-page status and strict-depth status, including exact thresholds or why strict depth was not used,
- validation performed, including link, course-note, formula-fence, and keyword checks, plus whether requested audit artifacts followed local guidance,
- repository/upload status when commit, push, or PR work was requested, and unresolved assumptions, noisy formulas, missing slides, or source files needing manual review.

If only a dry run or audit was requested, report planned changes and validation commands without writing notes.

## Bundled Resources

- `scripts/extract_pptx_text.py`: extract ordered text, tables, and notes from `.pptx` files.
- `scripts/convert_ppt_to_pptx.py`: convert legacy `.ppt` files to `.pptx` with LibreOffice.
- `scripts/extract_legacy_ppt_text.py`: extract best-effort OLE/CFB text records from legacy `.ppt` files when LibreOffice conversion is unavailable or fails.
- `scripts/extract_pdf_text.py`: extract raw text from `.pdf` courseware with backend fallback and extraction metadata.
- `scripts/check_obsidian_links.py`: check Markdown and Obsidian wiki links while ignoring fenced and inline code.
- `scripts/check_course_notes.py`: check course overview, review pages, empty files, conflict markers, template residue, and formula fences.
- `scripts/check_source_coverage.py`: check PPT/PDF source-file mapping, separate manifest/audit/note ownership evidence, sibling source parts, source-directory reconciliation, paper-note source ownership, page-level supplement index fields, source/generated example evidence, canonical source refs, chapter ownership, and hidden residue. Use `--strict` when the source root is available; it discovers same-name courses such as `cs231n` and reports unmapped source directories instead of silently omitting them.
- `scripts/clean_latex_from_ppt.py`: normalize formula and Unicode noise from slide extraction.
- `scripts/ppt_to_obsidian_pipeline.py`: run conversion, extraction, cleanup, and manifest creation.
- `references/obsidian-style.md`: local style guide for note writing and cross-linking.
- `references/validation.md`: lightweight validation checks for Obsidian Markdown outputs.
- `references/modes.md`: conversion mode guidance for course notes, group presentations, and exam review.
- `references/solvenotes-profile.md`: optional solvenotes-style validation and generated-artifact rules.
