#!/usr/bin/env python3
"""Remove provably redundant duplicated headings from drafted Obsidian notes.

Courseware notes often repeat a big heading: the note title (``# 经验风险与泛化``)
is echoed by the first section heading, or a slide banner that is copied onto
every slide turns into the same section title twice in a row. This tool removes
only headings that are redundant beyond doubt:

* two headings whose text is identical after normalization,
* separated by nothing but blank lines (no body content between them),
* where the later heading is at the same level or deeper than the earlier one.

A heading that repeats after real content is kept, because different sections may
legitimately share a title. YAML frontmatter and fenced code blocks are never
treated as headings. Writes are atomic.

Usage:
    python dedupe_headings.py notes/                 # fix in place
    python dedupe_headings.py notes/ --check         # exit 1 if any file would change
    python dedupe_headings.py notes/ --dry-run       # report only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXTENSIONS = (".md", ".markdown")

# Mirrors the ATX heading form used by ppt-to-md-for-obsidian/scripts/check_obsidian_links.py:
# up to three leading spaces, 1-6 hashes, then a space (or end of line).
ATX_HEADING_RE = re.compile(
    r"^(?P<indent>[ \t]{0,3})(?P<hashes>#{1,6})(?:[ \t]+(?P<title>.*?))?[ \t]*$"
)
CLOSING_HASHES_RE = re.compile(r"[ \t]+#+[ \t]*$")
EXPLICIT_ID_RE = re.compile(r"\s*\{#([^}\s]+)\}\s*$")
FENCE_RE = re.compile(r"^[ \t]*(?P<marker>`{3,}|~{3,})")
FRONTMATTER_OPEN_RE = re.compile(r"^\ufeff?---[ \t]*$")
FRONTMATTER_CLOSE_RE = re.compile(r"^(?:---|\.\.\.)[ \t]*$")
TRAILING_PUNCT = ":："


@dataclass(frozen=True)
class Heading:
    """A heading discovered in a note, by 0-based line index."""

    line_index: int
    level: int
    title: str

    @property
    def line_number(self) -> int:
        return self.line_index + 1


def normalize_heading(title: str) -> str:
    """Normalize a heading for comparison without changing its meaning."""

    text = unicodedata.normalize("NFKC", title)
    text = EXPLICIT_ID_RE.sub("", text)
    text = CLOSING_HASHES_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    text = text.rstrip(TRAILING_PUNCT).strip()
    return text.casefold()


def _masked_lines(lines: list[str]) -> list[bool]:
    """Mark lines that are frontmatter or inside a fenced code block."""

    masked = [False] * len(lines)
    index = 0
    if lines and FRONTMATTER_OPEN_RE.match(lines[0].rstrip("\r\n")):
        masked[0] = True
        index = 1
        while index < len(lines):
            masked[index] = True
            if FRONTMATTER_CLOSE_RE.match(lines[index].rstrip("\r\n")):
                index += 1
                break
            index += 1

    fence: tuple[str, int] | None = None
    while index < len(lines):
        raw = lines[index].rstrip("\r\n")
        match = FENCE_RE.match(raw)
        if match:
            marker = match.group("marker")
            if fence is None:
                fence = (marker[0], len(marker))
            elif fence[0] == marker[0] and len(marker) >= fence[1]:
                fence = None
            masked[index] = True
        elif fence is not None:
            masked[index] = True
        index += 1
    return masked


def _collect_headings(lines: list[str], masked: list[bool]) -> list[Heading]:
    headings: list[Heading] = []
    for index, raw in enumerate(lines):
        if masked[index]:
            continue
        match = ATX_HEADING_RE.match(raw.rstrip("\r\n"))
        if not match:
            continue
        title = match.group("title") or ""
        if not normalize_heading(title):
            continue
        headings.append(Heading(index, len(match.group("hashes")), title))
    return headings


def _has_content_between(
    lines: list[str], start: int, end: int, removed: set[int]
) -> bool:
    for index in range(start + 1, end):
        if index in removed:
            continue
        if lines[index].strip():
            return True
    return False


def find_duplicate_headings(text: str) -> list[Heading]:
    """Return the headings that are safe to delete, in document order."""

    lines = text.splitlines(keepends=True)
    masked = _masked_lines(lines)
    headings = _collect_headings(lines, masked)
    removed: set[int] = set()

    changed = True
    while changed:
        changed = False
        kept = [heading for heading in headings if heading.line_index not in removed]
        for earlier, later in zip(kept, kept[1:]):
            if normalize_heading(earlier.title) != normalize_heading(later.title):
                continue
            if later.level < earlier.level:
                continue
            if _has_content_between(lines, earlier.line_index, later.line_index, removed):
                continue
            removed.add(later.line_index)
            changed = True
            break

    return [heading for heading in headings if heading.line_index in removed]


def remove_duplicate_headings(text: str) -> tuple[str, list[Heading]]:
    """Return ``(new_text, removed_headings)``; ``new_text`` is unchanged if none."""

    lines = text.splitlines(keepends=True)
    duplicates = find_duplicate_headings(text)
    if not duplicates:
        return text, []

    removed = {heading.line_index for heading in duplicates}
    # Drop one redundant blank line left behind at the removal site.
    for index in sorted(removed):
        if index + 1 < len(lines) and index - 1 >= 0:
            if not lines[index - 1].strip() and not lines[index + 1].strip():
                removed.add(index + 1)

    kept_lines = [line for index, line in enumerate(lines) if index not in removed]
    return "".join(kept_lines), duplicates


def safe_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, preserving the existing mode."""

    directory = path.parent
    mode = path.stat().st_mode if path.exists() else None
    handle, temp_name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        if mode is not None:
            os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove redundant duplicated headings from drafted Obsidian notes."
    )
    parser.add_argument("paths", nargs="+", help="Files or directories to scan")
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Recurse into directories (default: on)",
    )
    parser.add_argument(
        "--ext",
        action="append",
        default=None,
        help="File extension to include (repeatable; default .md/.markdown)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    parser.add_argument("--check", action="store_true", help="Exit 1 if any file would change")
    parser.add_argument("--json", action="store_true", help="Print a JSON report")
    return parser.parse_args(argv)


def _describe(heading: Heading) -> dict:
    return {
        "line": heading.line_number,
        "level": heading.level,
        "title": heading.title.strip(),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    extensions = tuple(args.ext) if args.ext else DEFAULT_EXTENSIONS
    files = _collect_files([Path(p).expanduser() for p in args.paths], extensions, args.recursive)
    if not files:
        print("no matching files", file=sys.stderr)
        return 0

    report: list[dict] = []
    changed_files = 0
    total_removed = 0
    for file in files:
        try:
            # newline="" keeps CRLF notes intact instead of normalizing to LF.
            with open(file, encoding="utf-8", newline="") as stream:
                original = stream.read()
        except (OSError, UnicodeDecodeError) as exc:
            report.append({"file": str(file), "error": str(exc)})
            continue
        cleaned, duplicates = remove_duplicate_headings(original)
        if not duplicates:
            continue
        changed_files += 1
        total_removed += len(duplicates)
        entry = {"file": str(file), "removed": [_describe(item) for item in duplicates]}
        if not args.dry_run and not args.check:
            try:
                safe_write_text(file, cleaned)
                entry["written"] = True
            except OSError as exc:  # pragma: no cover - filesystem dependent
                entry["error"] = str(exc)
        report.append(entry)

    if args.json:
        print(json.dumps(
            {
                "files": len(files),
                "changed_files": changed_files,
                "removed_headings": total_removed,
                "report": report,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        mode = "would remove" if (args.dry_run or args.check) else "removed"
        for entry in report:
            if entry.get("error"):
                print(f"  ! {entry['file']}: {entry['error']}")
                continue
            if "removed" not in entry:
                continue
            count = len(entry["removed"])
            print(f"  {mode}: {entry['file']} ({count} heading(s))")
            for item in entry["removed"]:
                print(f"    - line {item['line']}: {'#' * item['level']} {item['title']}")
        print(f"\n{changed_files} file(s) {mode}, {total_removed} heading(s) total")

    if args.check and changed_files:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
