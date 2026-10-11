#!/usr/bin/env python3
"""Clean common LaTeX and Unicode noise from slide-extracted Markdown."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import unicodedata

try:
    from .safe_io import ensure_safe_input_file, read_bytes_no_follow, safe_write_text
except ImportError:
    from safe_io import ensure_safe_input_file, read_bytes_no_follow, safe_write_text


ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
LATEX_COMMANDS = (
    "alpha|beta|gamma|delta|epsilon|varepsilon|theta|lambda|mu|pi|rho|sigma|"
    "phi|varphi|omega|nabla|frac|sum|prod|sqrt|log|ln|exp|min|max|argmin|"
    "argmax|left|right|mathrm|mathbf|mathcal|mathbb|top|rightarrow|to|le|ge|"
    "times|cdot|infty|partial|Vert|lVert|rVert"
)
DOUBLE_COMMAND_RE = re.compile(r"\\{2,}(" + LATEX_COMMANDS + r")\b")

UNICODE_MATH = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ε": r"\epsilon",
    "θ": r"\theta",
    "λ": r"\lambda",
    "μ": r"\mu",
    "π": r"\pi",
    "ρ": r"\rho",
    "σ": r"\sigma",
    "φ": r"\phi",
    "ω": r"\omega",
    "∑": r"\sum",
    "∏": r"\prod",
    "√": r"\sqrt",
    "∇": r"\nabla",
    "∂": r"\partial",
    "≤": r"\le",
    "≥": r"\ge",
    "→": r"\to",
    "×": r"\times",
    "·": r"\cdot",
    "∞": r"\infty",
}


def line_looks_math(line: str) -> bool:
    math_markers = ("$", "\\", "=", "_", "^", "≤", "≥", "→", "∑", "∇", "∂")
    return any(marker in line for marker in math_markers)


def clean_line(line: str, unicode_math: bool) -> str:
    line = unicodedata.normalize("NFKC", line)
    line = ZERO_WIDTH_RE.sub("", line)
    line = CONTROL_RE.sub("", line)
    line = line.replace("﹨", "\\").replace("＼", "\\")
    line = DOUBLE_COMMAND_RE.sub(lambda match: "\\" + match.group(1), line)
    line = re.sub(r"\s+([,.;:，。；：])", r"\1", line)

    if unicode_math and line_looks_math(line):
        for src, dst in UNICODE_MATH.items():
            line = line.replace(src, dst)

    # Common extraction artifact: a backslash separated from the command.
    line = re.sub(r"\\\s+([A-Za-z]+)", r"\\\1", line)
    return line.rstrip()


def clean_text(text: str, unicode_math: bool = False) -> str:
    lines = [clean_line(line, unicode_math=unicode_math) for line in text.splitlines()]

    compacted = []
    blank_count = 0
    for line in lines:
        if line.strip():
            blank_count = 0
            compacted.append(line)
        else:
            blank_count += 1
            if blank_count <= 2:
                compacted.append("")

    return "\n".join(compacted).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean LaTeX and Unicode noise in Markdown.")
    parser.add_argument("input", type=Path, help="Input Markdown/text file")
    parser.add_argument("--out", type=Path, help="Output path. Defaults to stdout.")
    parser.add_argument(
        "--unicode-math",
        action="store_true",
        help="Convert common Unicode math symbols to LaTeX commands on math-like lines.",
    )
    args = parser.parse_args()

    try:
        input_path = ensure_safe_input_file(args.input)
        text = read_bytes_no_follow(input_path).decode("utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    cleaned = clean_text(text, unicode_math=args.unicode_math)
    if args.out:
        try:
            safe_write_text(args.out, cleaned)
        except (OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    else:
        print(cleaned, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
