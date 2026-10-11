#!/usr/bin/env python3
"""Convert legacy LaTeX math delimiters to Obsidian-renderable delimiters.

Obsidian renders inline math with ``$...$`` and block math with ``$$...$$``.
It does NOT render ``\\(...\\)`` or ``\\[...\\]``; those show up as literal text.
This tool rewrites old notes that used the legacy delimiters:

    \\[ ... \\]   ->   $$ ... $$     (block math, works across lines)
    \\( ... \\)   ->   $  ... $      (inline math)

It also normalizes ``prefix $$x$$ suffix`` onto their own lines (``--normalize-blocks``,
on by default) so Obsidian treats them as block math:

    $$x$$            ->   $$\n x \n $$
    text $$x$$ text  ->   text \n $$ \n x \n $$ \n text

Already-block ``$$`` delimiters are left as they are, and the tool never edits
inside fenced code blocks (```/~~~) or inline code spans (`...`). Writes are atomic.

Usage:
    python fix_math_delimiters.py notes/                # fix in place
    python fix_math_delimiters.py notes/ --check        # exit 1 if any file changes
    python fix_math_delimiters.py notes/ --dry-run      # report only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from safe_io import safe_write_text
except ImportError:  # pragma: no cover - fallback when run outside the skill
    safe_write_text = None

DEFAULT_EXTENSIONS = (".md", ".markdown")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")


def _split_inline_code(line: str) -> list[tuple[str, bool]]:
    """Split a line into (segment, is_code) pairs, honoring `...` code spans."""

    parts: list[tuple[str, bool]] = []
    index = 0
    length = len(line)
    while index < length:
        if line[index] == "`":
            run_end = index
            while run_end < length and line[run_end] == "`":
                run_end += 1
            ticks = line[index:run_end]
            close = line.find(ticks, run_end)
            if close == -1:
                parts.append((line[index:], True))
                break
            parts.append((line[index : close + len(ticks)], True))
            index = close + len(ticks)
        else:
            next_tick = line.find("`", index)
            if next_tick == -1:
                next_tick = length
            parts.append((line[index:next_tick], False))
            index = next_tick
    return parts


def _convert_plain(text: str, block: str | None) -> tuple[str, str | None, int]:
    result: list[str] = []
    index = 0
    length = len(text)
    changes = 0
    while index < length:
        if block is None:
            if text.startswith("$$", index):
                block = "dollar"
                result.append("$$")
                index += 2
                continue
            if text.startswith("\\[", index):
                block = "bracket"
                result.append("$$")
                index += 2
                changes += 1
                continue
            if text.startswith("\\(", index) or text.startswith("\\)", index):
                result.append("$")
                index += 2
                changes += 1
                continue
            result.append(text[index])
            index += 1
        else:
            closer = "$$" if block == "dollar" else "\\]"
            found = text.find(closer, index)
            if found == -1:
                result.append(text[index:])
                index = length
            else:
                result.append(text[index:found])
                result.append("$$")
                if block == "bracket":
                    changes += 1
                index = found + 2
                block = None
    return "".join(result), block, changes


def _convert_line(line: str, block: str | None) -> tuple[str, str | None, int]:
    if block is not None:
        return _convert_plain(line, block)
    out: list[str] = []
    changes = 0
    for segment, is_code in _split_inline_code(line):
        if is_code:
            out.append(segment)
            continue
        converted, block, count = _convert_plain(segment, block)
        out.append(converted)
        changes += count
    return "".join(out), block, changes


def convert_math_delimiters(text: str) -> tuple[str, int]:
    """Return (converted_text, number_of_delimiters_changed)."""

    out: list[str] = []
    fence: tuple[str, int] | None = None
    block: str | None = None
    changes = 0
    for raw_line in text.splitlines(keepends=True):
        if block is None:
            match = FENCE_RE.match(raw_line)
            if match:
                marker = match.group(1)
                if fence is None:
                    fence = (marker[0], len(marker))
                elif fence[0] == marker[0] and len(marker) >= fence[1]:
                    fence = None
                out.append(raw_line)
                continue
        if fence is not None:
            out.append(raw_line)
            continue
        converted, block, count = _convert_line(raw_line, block)
        out.append(converted)
        changes += count
    return "".join(out), changes


def _expand_inline_dollars(segment: str) -> tuple[str, int]:
    """Expand ``prefix $$content$$ suffix`` into ``$$`` blocks on their own lines."""

    pairs: list[tuple[int, int]] = []
    cursor = 0
    while True:
        start = segment.find("$$", cursor)
        if start == -1:
            break
        end = segment.find("$$", start + 2)
        if end == -1:
            break
        pairs.append((start, end))
        cursor = end + 2
    if not pairs:
        return segment, 0

    out: list[str] = []
    cursor = 0
    for start, end in pairs:
        prefix = segment[cursor:start].strip()
        content = segment[start + 2 : end].strip()
        if prefix:
            if out:
                out.append("\n")
            out.append(prefix)
            out.append("\n")
        out.append("$$\n")
        out.append(content)
        out.append("\n$$")
        cursor = end + 2
    tail = segment[cursor:].strip()
    if tail:
        if out:
            out.append("\n")
        out.append(tail)
    return "".join(out), len(pairs)


def _normalize_line(raw_line: str) -> tuple[str, int]:
    newline = ""
    body = raw_line
    if body.endswith("\r\n"):
        newline, body = "\r\n", body[:-2]
    elif body.endswith("\n"):
        newline, body = "\n", body[:-1]
    out: list[str] = []
    changes = 0
    for segment, is_code in _split_inline_code(body):
        if is_code:
            out.append(segment)
            continue
        converted, count = _expand_inline_dollars(segment)
        out.append(converted)
        changes += count
    return "".join(out) + newline, changes


def normalize_dollar_blocks(text: str) -> tuple[str, int]:
    """Put inline ``$$...$$`` on their own lines (Obsidian block math).

    Leaves already-block ``$$`` delimiters, fenced code blocks, and inline code
    spans untouched. Returns ``(normalized_text, blocks_expanded)``.
    """

    out: list[str] = []
    fence: tuple[str, int] | None = None
    in_block = False
    changes = 0
    for raw_line in text.splitlines(keepends=True):
        stripped = raw_line.strip()
        if not in_block:
            match = FENCE_RE.match(raw_line)
            if match:
                marker = match.group(1)
                if fence is None:
                    fence = (marker[0], len(marker))
                elif fence[0] == marker[0] and len(marker) >= fence[1]:
                    fence = None
                out.append(raw_line)
                continue
        if fence is not None:
            out.append(raw_line)
            continue
        if in_block:
            out.append(raw_line)
            if stripped == "$$":
                in_block = False
            continue
        if stripped == "$$":
            out.append(raw_line)
            in_block = True
            continue
        converted, count = _normalize_line(raw_line)
        out.append(converted)
        changes += count
    return "".join(out), changes


def _collect_files(paths: list[Path], extensions: tuple[str, ...], recursive: bool) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
            continue
        if not path.is_dir():
            continue
        pattern = "**/*" if recursive else "*"
        for candidate in sorted(path.glob(pattern)):
            if candidate.is_file() and candidate.suffix.lower() in extensions:
                files.append(candidate)
    seen: set[Path] = set()
    unique: list[Path] = []
    for file in files:
        key = file.resolve()
        if key in seen:
            continue
        seen.add(key)
        unique.append(file)
    return unique


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert legacy math delimiters to Obsidian form.")
    parser.add_argument("paths", nargs="+", help="Files or directories to fix")
    parser.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True,
                        help="Recurse into directories (default: on)")
    parser.add_argument("--ext", action="append", default=None,
                        help="File extension to include (repeatable; default .md/.markdown)")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    parser.add_argument("--check", action="store_true", help="Exit 1 if any file would change")
    parser.add_argument(
        "--normalize-blocks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also expand inline $$...$$ onto their own lines (default: on)",
    )
    parser.add_argument("--json", action="store_true", help="Print a JSON report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    extensions = tuple(args.ext) if args.ext else DEFAULT_EXTENSIONS
    files = _collect_files([Path(p).expanduser() for p in args.paths], extensions, args.recursive)
    if not files:
        print("no matching files", file=sys.stderr)
        return 0

    report: list[dict] = []
    total_changes = 0
    changed_files = 0
    for file in files:
        try:
            original = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.append({"file": str(file), "error": str(exc)})
            continue
        converted, changes = convert_math_delimiters(original)
        if args.normalize_blocks:
            converted, extra = normalize_dollar_blocks(converted)
            changes += extra
        if changes == 0:
            continue
        changed_files += 1
        total_changes += changes
        entry = {"file": str(file), "changes": changes}
        if not args.dry_run and not args.check:
            try:
                if safe_write_text is not None:
                    safe_write_text(file, converted)
                else:
                    file.write_text(converted, encoding="utf-8")
                entry["written"] = True
            except Exception as exc:  # pragma: no cover - filesystem dependent
                entry["error"] = str(exc)
        report.append(entry)

    if args.json:
        print(json.dumps(
            {"files": len(files), "changed_files": changed_files, "changes": total_changes, "report": report},
            ensure_ascii=False, indent=2,
        ))
    else:
        mode = "would change" if (args.dry_run or args.check) else "changed"
        for entry in report:
            if entry.get("error"):
                print(f"  ! {entry['file']}: {entry['error']}")
            elif "changes" in entry:
                print(f"  {mode}: {entry['file']} ({entry['changes']} delimiter(s))")
        print(f"\n{changed_files} file(s) {mode}, {total_changes} delimiter(s) total")

    if args.check and changed_files:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
