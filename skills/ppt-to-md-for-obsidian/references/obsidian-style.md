# Obsidian Note Style

## Naming

- Use numbered files for ordered course chapters: `01_绪论.md`, `02_搜索策略.md`.
- Use `00_课程总览.md` or `00_学习地图.md` as the entry page.
- Keep review files explicit:
  - `知识点详细版_含公式.md`
  - `知识点精简复习版_含公式.md`

## Writing

- Write in Chinese by default.
- Keep standard English technical terms when they are the normal name.
- Expand slide bullets into coherent paragraphs.
- Do not keep generic slide phrases if they do not help learning.
- Add examples or boundary cases when a concept is easy to misuse.

## Formulas

- Use block math:

```markdown
$$
L(\theta)=\frac{1}{N}\sum_{i=1}^{N}\ell(f_\theta(x_i), y_i)
$$
```

- Explain variables immediately after the formula.
- Explain what the formula is used for in the current topic.
- Avoid copying formulas without context.

## Links

- Prefer Obsidian wiki links:

```markdown
[[机器学习/05_模型的泛化能力|泛化能力]]
```

- Put links where the related concept first appears.
- Avoid repeated trailing link dumps.
- Navigation pages are allowed to be link-heavy.

## Review Pages

The detailed version should be comprehensive. The concise version should be fast to scan:

- main chain,
- core formulas,
- frequent mistakes,
- links back to detailed version and course overview.

## Mode Differences

- Course notes should preserve chapter order and concept progression.
- Research group presentations should emphasize problem, method, experiment, limitation, and discussion.
- Exam review notes should prioritize formula tables, typical question patterns, and common mistakes.
