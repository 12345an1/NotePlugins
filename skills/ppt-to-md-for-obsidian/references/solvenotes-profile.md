# Solvenotes Profile

Use this reference only when the target vault or repository clearly follows solvenotes-style conventions, for example local guidance names solvenotes or project scripts define the same checks.

## Guidance Boundary

- Inside the Solvenotes vault, root `AGENT.md` is the only guidance file. Detailed project rules live in `solvenotes/agent/`, alongside the vault rather than inside it.
- Do not create `notes/agent/`, treat it as course content, or add ordinary Obsidian navigation links to project-rule pages.
- Read the vault-root `AGENT.md` first, then follow any repository-level rule files it explicitly points to.

## Quality Checks

- Follow the Audit Output Placement rule in `SKILL.md`: keep temporary reports outside the vault and write corrections and source markers into notes.
- Keep the applicable self-contained `source_manifest.md` outside Notes and outside the public Skills repository. Use `SOLVENOTES_MANIFEST_ROOT`, or default to `Notes.parent / "vault_sources"`; mirror the course/topic path relative to Notes. Reject roots inside Notes, including symlink resolutions. Synthetic public fixtures may use `fixtures/vault_sources`.
- Resolve manifest note targets against the corresponding Notes directory. Preserve source paths and types, extraction methods, unit counts, mapping targets, status, dates, example evidence, and OCR/blank/visual limits. Keep `source_files`, source URLs, and page/slide markers in the learning notes.
- Missing, unreadable, or empty required external manifests are FAIL/unavailable, never an empty PASS. Mapping ranges do not prove per-unit semantic or visual verification.
- Do not create or update `99_内容覆盖审查.md`, `coverage_audit`, or a central coverage page. Keep temporary audit ledgers and machine-readable reports outside the vault.
- Do not infer semantic completion from a source-to-note range or aggregate mapping. Separate extractability, mapping, and semantic verification, and never claim OCR or visual coverage that was not performed.

## Solvenotes Validation

When project-local validators exist, prefer them over bundled generic checks. A typical solvenotes-style local suite may include:

- `check_all_notes.py`
- `check_links.py`
- `check_examples.py`
- `check_frontmatter.py`
- `check_markdown_tables.py`
- `check_formulas.py`
- `check_headings.py`
- `check_special_dirs.py`
- `check_source_coverage.py`
- `check_source_files.py --strict` with `SOLVENOTES_SOURCE_ROOT` set to the source repository root
- generated-file `--check` commands

Do not pass the bundled generic `--require-coverage-audit` option in Solvenotes. The external maintainer coverage checker validates the external manifest contract and rejects legacy audit pages and in-vault manifests.

## Source Coverage

- Keep the independent top-level note systems `概念索引`, `模板`, `游戏数值策划`, `科研方法论`, `算法岗学习笔记`, and `学习路径` outside course-to-source directory reconciliation.
- Run the external maintainer `check_source_coverage.py` with the resolved manifest root first; require every formal manifest row to satisfy the current local schema and target-link contract.
- Set `SOLVENOTES_SOURCE_ROOT` and run the project-local `check_source_files.py --strict` to verify source existence, declared unit counts, extractability, and explicitly recorded blank/OCR/visual limitations.
- Treat a missing source root or required external manifest store as unavailable source verification, not as a passing coverage result. Preserve honest no-extractable-text findings when the manifest already records their boundary.
- After migrating source index lines between notes, rerun source coverage checks and a direct `rg` for moved source filenames in old target notes.

## Repository Hygiene

- Before upload, run local repository hygiene checks when available.
- Confirm local trash, workspace state, caches, and package-export outputs are ignored or excluded by project scripts.
- Do not stage `.obsidian/workspace.json` or other local UI state.
- If both a notes repository and a skill repository changed, validate and report them separately.
