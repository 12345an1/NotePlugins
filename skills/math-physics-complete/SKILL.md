---
name: math-physics-complete
description: "Use for formula-heavy work in mathematics, theoretical physics, engineering physics, control theory, mechanics, electromagnetism, and quantum/statistical/continuum physics, and for reading equations in papers. Produces complete rendered formulas with a symbol table, explicit assumptions and units, coordinate systems, boundary and initial conditions, and dimensional or limiting-case checks."
---

# Math Physics Complete

A working contract for formula-heavy answers: every symbol defined, every assumption stated, every equation whole, and every result checked.

## 1. Math formatting

- Delimit inline math with `$...$` and display math with `$$...$$`, each `$$` on its own line (this is what Obsidian and MathJax render).
- Never wrap formulas in fenced code blocks unless raw LaTeX source is explicitly requested.
- Write real math instead of ASCII stand-ins: `$\dot{x}=Ax+Bu$`, not `x_dot = Ax + Bu`.
- Keep every meaning-bearing annotation: sub/superscripts, arrows, hats, bars, tildes, primes, transposes, tensor indices, and the bounds of sums and integrals.
- Reach for `aligned`, `cases`, matrices, and named operators when they make a long expression readable.

## 2. Answer contract

For a mathematical or physical question, the answer should:

1. Restate the problem in ordinary language before touching symbols.
2. Fix the frame: coordinate system, basis, units, sign convention, and domain where they matter.
3. List the assumptions and the range over which the result holds.
4. Introduce each symbol at first use — indices, constants, operators, fields, and parameters included.
5. Give formulas whole: no dropped limits, domains, initial or boundary data, or normalization.
6. Show the working for a derivation unless a short answer was requested.
7. Split long derivations into labelled stages; skip algebra only when it is genuinely routine and unrelated to the question.
8. Validate the endpoint: dimensions, signs, special and limiting cases, and agreement with the assumptions.
9. Name what is still ambiguous or missing instead of quietly filling the gap.

## 3. Symbol table

For any substantial answer, add a short symbol table before or after the derivation. Minimum columns: symbol, meaning, unit or dimension, and a note or assumption. When many symbols appear, group them (coordinates, fields, parameters, operators, indices, boundary data).

## 4. Reading a formula from a paper

Reproduce the equation with its original number, then explain it in labelled parts:

- **Equation (n-m)** — the formula as printed.
- **Symbols** — one line per symbol, with units.
- **Purpose** — why the paper introduces it and how it connects to the equations around it.
- **Checks** — dimensions, signs, assumptions, and degenerate cases.

If the paper's notation is inconsistent, say so and choose one convention for the explanation. Do not renumber the paper's equations; label anything you add as a local equation.

## 5. Long derivations

Default spine: setup and assumptions → frame and units → symbol definitions → governing equations → step-by-step working → boundary or initial conditions → result → checks and meaning. If a derivation is very long, pause at a clean break and state what comes next instead of compressing the rest into a hand-wave.

## 6. Pre-delivery checklist

- Inline `$...$`, display `$$...$$`; no `\(...\)` or `\[...\]` (Obsidian renders those as literal text). For notes that still use the old delimiters, run `ppt-to-md-for-obsidian/scripts/fix_math_delimiters.py`.
- Every symbol in a formula is defined inline or listed in the symbol table.
- Physical quantities carry units or dimensions.
- Differential-equation answers state boundary or initial conditions.
- Sums and integrals show their ranges.
- At least one sanity check (dimension or limiting case) is present when it applies.
