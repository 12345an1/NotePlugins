"""Parse inline Markdown link destinations without a Markdown dependency."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

MAX_DESTINATION_PAREN_DEPTH = 32
ESCAPABLE_PUNCTUATION_RE = re.compile(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])")
# Guard the single-CR and single-LF alternatives so a CRLF cannot backtrack
# and be reinterpreted as two separate line endings.
LINE_ENDING_PATTERN = r"(?:\r\n|\r(?!\n)|(?<!\r)\n)"
BLANK_LINE_RE = re.compile(LINE_ENDING_PATTERN + r"[ \t]*" + LINE_ENDING_PATTERN)


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _label_end(text: str, start: int, end: int) -> int | None:
    depth = 1
    cursor = start + 1
    while cursor < end:
        char = text[cursor]
        if char in "\r\n" and BLANK_LINE_RE.match(text, cursor):
            return None
        if char == "\\" and cursor + 1 < end:
            cursor += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return None


def _skip_separator_space(text: str, start: int, end: int) -> int | None:
    """Skip spaces/tabs and at most one line ending between link parts."""

    cursor = start
    line_endings = 0
    while cursor < end and text[cursor] in " \t\r\n":
        if text[cursor] == "\r":
            line_endings += 1
            cursor += 1
            if cursor < end and text[cursor] == "\n":
                cursor += 1
            continue
        if text[cursor] == "\n":
            line_endings += 1
        cursor += 1
    return None if line_endings > 1 else cursor


def _angle_destination_end(text: str, start: int, end: int) -> int | None:
    cursor = start + 1
    while cursor < end:
        char = text[cursor]
        if char in "\r\n" or (char == "<" and not _is_escaped(text, cursor)):
            return None
        if char == ">" and not _is_escaped(text, cursor):
            return cursor + 1
        cursor += 1
    return None


def _bare_destination_end(text: str, start: int, end: int) -> tuple[int, int] | None:
    cursor = start
    depth = 0
    while cursor < end:
        char = text[cursor]
        if (
            char == "\\"
            and cursor + 1 < end
            and ESCAPABLE_PUNCTUATION_RE.fullmatch(text[cursor : cursor + 2])
        ):
            cursor += 2
            continue
        if char in " \t\r\n":
            return cursor, depth
        if ord(char) < 32 or ord(char) == 127:
            return None
        if char == "(":
            depth += 1
            if depth > MAX_DESTINATION_PAREN_DEPTH:
                return None
        elif char == ")":
            if depth == 0:
                return cursor, depth
            depth -= 1
        cursor += 1
    return None


def _title_end(text: str, start: int, end: int) -> int | None:
    opener = text[start]
    closer = ")" if opener == "(" else opener
    cursor = start + 1
    while cursor < end:
        if BLANK_LINE_RE.match(text, cursor):
            return None
        char = text[cursor]
        if char == "\\" and cursor + 1 < end:
            cursor += 2
            continue
        if char == closer:
            return cursor + 1
        cursor += 1
    return None


def _parse_link_at(
    text: str,
    start: int,
    end: int,
    *,
    images: bool,
) -> tuple[str, int] | None:
    is_image = start > 0 and text[start - 1] == "!" and not _is_escaped(text, start - 1)
    if images != is_image:
        return None
    label_end = _label_end(text, start, end)
    if label_end is None or label_end + 1 >= end or text[label_end + 1] != "(":
        return None

    content_start = label_end + 2
    cursor = _skip_separator_space(text, content_start, end)
    if cursor is None or cursor >= end:
        return None
    target_start = cursor
    if text[cursor] == ")":
        return "", cursor + 1
    if text[cursor] == "<":
        target_end = _angle_destination_end(text, cursor, end)
        if target_end is None:
            return None
        cursor = target_end
    else:
        parsed = _bare_destination_end(text, cursor, end)
        if parsed is None:
            return None
        target_end, remaining_depth = parsed
        if remaining_depth:
            return None
        cursor = target_end

    target = text[target_start:target_end]
    if cursor < end and text[cursor] == ")":
        return target, cursor + 1
    separator_end = _skip_separator_space(text, cursor, end)
    if separator_end is None or separator_end == cursor or separator_end >= end:
        return None
    cursor = separator_end
    if text[cursor] == ")":
        return target, cursor + 1
    if text[cursor] not in "\"'(":
        return None
    cursor = _title_end(text, cursor, end)
    if cursor is None:
        return None
    close_start = _skip_separator_space(text, cursor, end)
    if close_start is None or close_start >= end or text[close_start] != ")":
        return None
    return target, close_start + 1


@dataclass(frozen=True)
class MarkdownLinkMatch:
    source: str
    target: str
    start_index: int
    end_index: int

    def group(self, index: int = 0) -> str:
        if index == 0:
            return self.source[self.start_index : self.end_index]
        if index == 1:
            return self.target
        raise IndexError("no such group")

    def start(self) -> int:
        return self.start_index

    def end(self) -> int:
        return self.end_index


class MarkdownLinkPattern:
    """Small ``re.Pattern``-like adapter used by existing consumers."""

    def __init__(self, *, images: bool = False) -> None:
        self.images = images

    def finditer(self, text: str, pos: int = 0, endpos: int | None = None) -> Iterator[MarkdownLinkMatch]:
        end = len(text) if endpos is None else min(endpos, len(text))
        cursor = max(0, pos)
        while cursor < end:
            start = text.find("[", cursor, end)
            if start < 0:
                return
            if _is_escaped(text, start):
                cursor = start + 1
                continue
            parsed = _parse_link_at(text, start, end, images=self.images)
            if parsed is None:
                cursor = start + 1
                continue
            target, match_end = parsed
            yield MarkdownLinkMatch(text, target, start, match_end)
            cursor = match_end

    def findall(self, text: str, pos: int = 0, endpos: int | None = None) -> list[str]:
        return [match.group(1) for match in self.finditer(text, pos, endpos)]


MARKDOWN_LINK_RE = MarkdownLinkPattern()
MARKDOWN_IMAGE_RE = MarkdownLinkPattern(images=True)


def unescape_markdown_destination(value: str) -> str:
    """Apply CommonMark backslash escapes for ASCII punctuation."""

    return ESCAPABLE_PUNCTUATION_RE.sub(r"\1", value)


def split_destination_suffix(value: str) -> str:
    """Remove an unescaped query/fragment before percent decoding."""

    for index, char in enumerate(value):
        if char in "?#" and not _is_escaped(value, index):
            return value[:index]
    return value
