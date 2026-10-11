#!/usr/bin/env python3
"""Check Markdown and Obsidian wiki links in a vault-like directory."""

from __future__ import annotations

import argparse
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

try:
    from .markdown_links import (
        MARKDOWN_IMAGE_RE,
        MARKDOWN_LINK_RE,
        unescape_markdown_destination,
    )
except ImportError:
    try:
        from .shared.markdown_links import (
            MARKDOWN_IMAGE_RE,
            MARKDOWN_LINK_RE,
            unescape_markdown_destination,
        )
    except ImportError:
        try:
            from markdown_links import (
                MARKDOWN_IMAGE_RE,
                MARKDOWN_LINK_RE,
                unescape_markdown_destination,
            )
        except ImportError:
            from shared.markdown_links import (
                MARKDOWN_IMAGE_RE,
                MARKDOWN_LINK_RE,
                unescape_markdown_destination,
            )

try:
    from .safe_io import ensure_safe_input_directory
except ImportError:
    try:
        from .shared.safe_io import ensure_safe_input_directory
    except ImportError:
        try:
            from safe_io import ensure_safe_input_directory
        except ImportError:
            from shared.safe_io import ensure_safe_input_directory

WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
WIKI_LINK_CHECK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
LINE_ENDING_PATTERN = r"(?:\r\n|\r(?!\n)|(?<!\r)\n)"
BLANK_LINE_RE = re.compile(LINE_ENDING_PATTERN + r"[ \t]*" + LINE_ENDING_PATTERN)
URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
ATX_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}(?:[ \t]+|$)(?P<title>.*?)\s*$")
SETEXT_HEADING_RE = re.compile(r"^[ \t]{0,3}(?P<marker>=+|-+)[ \t]*$")
EXPLICIT_HEADING_ID_RE = re.compile(r"\s*\{#([^}\s]+)\}\s*$")
FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[^\n]*$")
FENCE_CLOSE_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[ \t]*$")
BLOCK_MATH_DELIMITER_RE = re.compile(r"^[ \t]*\$\$[ \t]*$")
COMMENT_SPAN_RE = re.compile(r"<!--.*?(?:-->|\Z)|%%.*?(?:%%|\Z)", re.DOTALL)
LIST_ITEM_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>[-+*]|\d{1,9}[.)])(?:(?P<spacing>[ \t]+)|$)"
)
BLOCK_ID_LINE_RE = re.compile(r"(?:^|[ \t])\^([A-Za-z0-9-]+)[ \t]*$")
REFERENCE_DEFINITION_OPEN_RE = re.compile(r"^ {0,3}\[")
MARKDOWN_PUNCTUATION = frozenset('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~')


@dataclass
class ListContext:
    content_indent: int
    has_body: bool


@dataclass(frozen=True)
class ListItemLayout:
    marker_indent: int
    content_indent: int
    body_index: int
    has_body: bool


@dataclass(frozen=True)
class FenceContext:
    marker: str
    length: int
    quote_depth: int
    list_indents: tuple[int, ...]


def indentation_columns(value: str, start: int = 0) -> int:
    columns = start
    for char in value:
        if char == " ":
            columns += 1
        elif char == "\t":
            columns += 4 - (columns % 4)
        else:
            break
    return columns


def leading_whitespace(line: str) -> tuple[int, int]:
    index = 0
    while index < len(line) and line[index] in " \t":
        index += 1
    return index, indentation_columns(line[:index])


def blockquote_prefix(line: str, max_depth: int | None = None) -> tuple[int, int]:
    """Return explicit CommonMark blockquote depth and consumed character count."""

    depth = 0
    cursor = 0
    while max_depth is None or depth < max_depth:
        marker = cursor
        spaces = 0
        while marker < len(line) and line[marker] == " " and spaces < 3:
            marker += 1
            spaces += 1
        if marker >= len(line) or line[marker] != ">":
            break
        cursor = marker + 1
        if cursor < len(line) and line[cursor] in " \t":
            cursor += 1
        depth += 1
    return depth, cursor


def is_thematic_break(line: str) -> bool:
    whitespace_end, indent = leading_whitespace(line)
    if indent > 3:
        return False
    compact = re.sub(r"[ \t]", "", line[whitespace_end:])
    return len(compact) >= 3 and len(set(compact)) == 1 and compact[0] in "*-_"


def list_item_layout(line: str) -> ListItemLayout | None:
    """Return CommonMark list layout, including empty-marker items."""

    if is_thematic_break(line):
        return None

    match = LIST_ITEM_RE.match(line)
    if match is None:
        return None
    marker_indent = indentation_columns(match.group("indent"))
    marker_end = marker_indent + len(match.group("marker"))
    marker_end_index = len(match.group("indent")) + len(match.group("marker"))
    spacing = match.group("spacing") or ""
    unpadded_body_index = marker_end_index + len(spacing)
    has_body = bool(line[unpadded_body_index:].strip())
    if not has_body:
        # CommonMark gives an empty list item W + 1 columns of padding,
        # regardless of how much trailing whitespace follows its marker.
        padding = 1
        body_index = unpadded_body_index
    elif not spacing:
        padding = 1
        body_index = marker_end_index
    else:
        spacing_end = indentation_columns(spacing, start=marker_end)
        padding = spacing_end - marker_end
        if 1 <= padding <= 4:
            body_index = marker_end_index + len(spacing)
        else:
            padding = 1
            body_index = marker_end_index + 1
    return ListItemLayout(
        marker_indent=marker_indent,
        content_indent=marker_end + padding,
        body_index=body_index,
        has_body=has_body,
    )


def updated_list_stack(
    stack: list[ListContext],
    layout: ListItemLayout,
) -> list[ListContext] | None:
    """Return an updated list-container stack, or None when layout is code text."""

    updated = list(stack)
    while updated and layout.marker_indent < updated[-1].content_indent:
        updated.pop()
    container_indent = updated[-1].content_indent if updated else 0
    if not 0 <= layout.marker_indent - container_indent <= 3:
        return None
    updated.append(
        ListContext(
            content_indent=layout.content_indent,
            has_body=layout.has_body,
        )
    )
    return updated


def list_continuation_view(
    line: str,
    stack: list[ListContext],
    previous_blank: bool,
) -> tuple[str, int, list[ListContext]]:
    """Strip an active list container and return logical content plus prefix width."""

    if not line.strip():
        return "", len(line), stack
    whitespace_end, indent = leading_whitespace(line)
    matching_context = next(
        (index for index in range(len(stack) - 1, -1, -1) if indent >= stack[index].content_indent),
        None,
    )
    if matching_context is not None:
        updated = stack[: matching_context + 1]
        relative_indent = indent - updated[-1].content_indent
        logical = " " * relative_indent + line[whitespace_end:]
        return logical, whitespace_end, updated
    if previous_blank:
        return line, 0, []
    return line, 0, stack


def mask_prefix(value: str, end: int) -> str:
    characters = list(value)
    for position in range(min(end, len(characters))):
        if characters[position] not in "\r\n":
            characters[position] = " "
    return "".join(characters)


def text_without_code(
    text: str,
    *,
    report_unclosed: bool = False,
) -> str | tuple[str, bool]:
    """Mask CommonMark code and expose non-code container bodies in place.

    A closing fence may be longer than its opener (CommonMark), so a single
    backreference-based regular expression is not sufficient.  The scanner
    removes blockquote and list container prefixes before classifying fenced
    or indented code, while keeping output line and character positions stable.
    """

    def mask(value: str) -> str:
        return "".join("\n" if char == "\n" else " " for char in value)

    masked_lines: list[str] = []
    inline_boundaries: set[int] = {0, len(text)}
    source_offset = 0
    fence: FenceContext | None = None
    unclosed_fence = False
    list_stack: list[ListContext] = []
    active_quote_depth = 0
    in_indented_code = False
    previous_blank = True
    for line in text.splitlines(keepends=True):
        line_start = source_offset
        source_offset += len(line)
        raw = line.rstrip("\r\n")

        if fence is not None:
            required_quote_depth = fence.quote_depth
            quote_depth, quote_end = blockquote_prefix(raw, max_depth=required_quote_depth)
            if quote_depth == required_quote_depth:
                content = raw[quote_end:]
                remains_in_list = True
                if fence.list_indents and content.strip():
                    _whitespace_end, indent = leading_whitespace(content)
                    active_indents = tuple(
                        context.content_indent
                        for context in list_stack[: len(fence.list_indents)]
                    )
                    remains_in_list = (
                        active_indents == fence.list_indents
                        and indent >= fence.list_indents[-1]
                    )
                if remains_in_list:
                    logical, _list_prefix, list_stack = list_continuation_view(
                        content,
                        list_stack,
                        previous_blank=False,
                    )
                    inline_boundaries.update((line_start, source_offset))
                    masked_lines.append(mask(line))
                    closing = FENCE_CLOSE_RE.fullmatch(logical)
                    if (
                        closing is not None
                        and closing.group("fence")[0] == fence.marker
                        and len(closing.group("fence")) >= fence.length
                    ):
                        fence = None
                    previous_blank = not content.strip()
                    continue

                # A nonblank outdent closes a fence opened in a list. Keep
                # only any outer list containers that the current line still
                # belongs to, then process this same line as ordinary Markdown.
                unclosed_fence = True
                fence = None
                in_indented_code = False
                _whitespace_end, indent = leading_whitespace(content)
                matching_context = next(
                    (
                        index
                        for index in range(len(list_stack) - 1, -1, -1)
                        if indent >= list_stack[index].content_indent
                    ),
                    None,
                )
                list_stack = (
                    list_stack[: matching_context + 1]
                    if matching_context is not None
                    else []
                )
            else:
                unclosed_fence = True
                fence = None
                list_stack = []
                in_indented_code = False

        quote_depth, quote_end = blockquote_prefix(raw)
        if quote_depth != active_quote_depth:
            inline_boundaries.add(line_start)
            list_stack = []
            in_indented_code = False
            active_quote_depth = quote_depth
        content = raw[quote_end:]
        prefix_end = quote_end
        line_list_indents: tuple[int, ...] = ()

        layout = list_item_layout(content)
        if layout is not None:
            next_stack = updated_list_stack(list_stack, layout)
        else:
            next_stack = None
        if next_stack is not None:
            inline_boundaries.add(line_start)
            list_stack = next_stack
            logical = content[layout.body_index:]
            prefix_end = quote_end + layout.body_index
            line_list_indents = tuple(context.content_indent for context in list_stack)
        else:
            logical, list_prefix, list_stack = list_continuation_view(
                content,
                list_stack,
                previous_blank=previous_blank,
            )
            prefix_end = quote_end + list_prefix
            if list_prefix:
                line_list_indents = tuple(context.content_indent for context in list_stack)

        if not logical.strip():
            if in_indented_code:
                masked_lines.append(mask(line))
            else:
                masked_lines.append(mask_prefix(line, prefix_end))
            previous_blank = True
            continue

        opening = FENCE_OPEN_RE.fullmatch(logical)
        if opening is not None:
            inline_boundaries.update((line_start, source_offset))
            token = opening.group("fence")
            masked_lines.append(mask(line))
            if not line_list_indents:
                list_stack = []
            fence = FenceContext(
                marker=token[0],
                length=len(token),
                quote_depth=quote_depth,
                list_indents=line_list_indents,
            )
            in_indented_code = False
            if list_stack:
                list_stack[-1].has_body = True
            previous_blank = False
            continue

        logical_indent = indentation_columns(logical)
        if in_indented_code and logical_indent >= 4:
            inline_boundaries.update((line_start, source_offset))
            masked_lines.append(mask(line))
            previous_blank = False
            continue
        in_indented_code = False

        can_start_indented_code = logical_indent >= 4 and (
            not list_stack or previous_blank or not list_stack[-1].has_body
        )
        if can_start_indented_code:
            inline_boundaries.update((line_start, source_offset))
            masked_lines.append(mask(line))
            in_indented_code = True
            if list_stack:
                list_stack[-1].has_body = True
            previous_blank = False
            continue

        if list_stack:
            list_stack[-1].has_body = True
        logical_stripped = logical.strip()
        if (
            re.match(r"^#{1,6}(?:[ \t]+|$)", logical.lstrip(" \t"))
            or re.fullmatch(r"(?:=+|-+)", logical_stripped)
            or re.fullmatch(r"(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,}", logical_stripped)
        ):
            inline_boundaries.update((line_start, source_offset))
        masked_lines.append(mask_prefix(line, prefix_end))
        previous_blank = False

    # Inline code spans are inline-level constructs but may cross a soft line
    # break.  Apply their state machine to the complete, block-code-masked
    # document instead of resetting it for every physical line.
    masked = mask_inline_code("".join(masked_lines), boundaries=inline_boundaries)
    if report_unclosed:
        return masked, unclosed_fence or fence is not None
    return masked


def count_block_math_delimiters(text: str) -> int:
    """Count standalone ``$$`` lines outside Markdown and Obsidian comments.

    Callers pass text already processed by :func:`text_without_code`, which
    masks code and CommonMark container prefixes in place.  Requiring the
    delimiter to occupy the remaining logical line avoids treating prose,
    escaped literals, or comment examples as block-math structure.
    """

    visible = COMMENT_SPAN_RE.sub(
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        text,
    )
    return sum(bool(BLOCK_MATH_DELIMITER_RE.fullmatch(line)) for line in visible.splitlines())


def _inline_code_segments(
    text: str,
    boundaries: set[int] | None = None,
) -> list[tuple[int, int]]:
    """Return inline-block ranges separated by CommonMark blank lines.

    A code span may cross a soft line break, but it cannot cross the blank line
    that ends its inline block.  Keeping the separator outside every range also
    ensures that an unmatched opener cannot hide later prose in a new block.
    """

    split_points = {0, len(text), *(boundaries or set())}
    for boundary in BLANK_LINE_RE.finditer(text):
        split_points.update((boundary.start(), boundary.end()))
    ordered = sorted(point for point in split_points if 0 <= point <= len(text))
    return [
        (start, end)
        for start, end in zip(ordered, ordered[1:])
        if start < end
    ]


def mask_inline_code(
    text: str,
    *,
    boundaries: set[int] | None = None,
) -> str:
    """Mask paired CommonMark backtick code spans across soft line breaks.

    Code spans close with a backtick run of the same length as the opener;
    shorter runs may occur inside the span.  Pairing runs explicitly avoids
    treating those interior runs as an early close (which a simple regular
    expression would do).  Pairing is reset at blank-line block boundaries,
    and unmatched runs are left untouched so ordinary later prose is checked.
    """

    runs: list[tuple[int, int, int]] = []
    spans: list[tuple[int, int]] = []
    for segment_start, segment_end in _inline_code_segments(text, boundaries):
        runs.clear()
        index = segment_start
        while index < segment_end:
            if text[index] != "`":
                index += 1
                continue
            end = index + 1
            while end < segment_end and text[end] == "`":
                end += 1
            runs.append((index, end, end - index))
            index = end

        run_index = 0
        while run_index < len(runs) - 1:
            start, _end, length = runs[run_index]
            close_index = next(
                (
                    candidate
                    for candidate in range(run_index + 1, len(runs))
                    if runs[candidate][2] == length
                ),
                None,
            )
            if close_index is None:
                run_index += 1
                continue
            spans.append((start, runs[close_index][1]))
            run_index = close_index + 1

    if not spans:
        return text
    characters = list(text)
    for start, end in spans:
        for position in range(start, end):
            if characters[position] != "\n":
                characters[position] = " "
    return "".join(characters)


def _mask_yaml_frontmatter(text: str) -> str:
    """Hide a leading Obsidian YAML frontmatter block from Markdown parsing."""

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].lstrip("\ufeff").rstrip("\r\n").strip() != "---":
        return text

    offset = len(lines[0])
    for line in lines[1:]:
        if line.rstrip("\r\n").strip() == "---":
            end = offset + len(line)
            prefix = text[:end]
            masked = "".join("\n" if char == "\n" else "\r" if char == "\r" else " " for char in prefix)
            return masked + text[end:]
        offset += len(line)
    return text


def _mask_comment_spans(text: str) -> str:
    """Mask HTML and Obsidian comments while preserving line boundaries."""

    return COMMENT_SPAN_RE.sub(
        lambda match: "".join(char if char in "\r\n" else " " for char in match.group()),
        text,
    )


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _is_wiki_search_target(target: str) -> bool:
    """Return whether a wikilink is a dynamic search query, not a fixed target."""

    value = target.strip()
    return value.startswith("##") or value.startswith("^^")


def _is_markdown_escape(text: str, index: int) -> bool:
    return text[index] in MARKDOWN_PUNCTUATION and _is_escaped(text, index)


def _reference_label_end(text: str, start: int, *, allow_nested: bool) -> int | None:
    """Return the closing bracket for a CommonMark link label."""

    if start >= len(text) or text[start] != "[":
        return None
    depth = 1
    cursor = start + 1
    while cursor < len(text):
        if BLANK_LINE_RE.match(text, cursor):
            return None
        char = text[cursor]
        if char == "\\" and cursor + 1 < len(text) and text[cursor + 1] in MARKDOWN_PUNCTUATION:
            cursor += 2
            continue
        if char == "[":
            if not allow_nested:
                return None
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return cursor if cursor - start - 1 <= 999 else None
        cursor += 1
    return None


def _normalize_reference_label(label: str) -> str:
    """Apply CommonMark's case-folding and whitespace normalization to a label."""

    stripped = label.strip(" \t\r\n")
    return re.sub(r"[ \t\r\n]+", " ", stripped).casefold()


def _skip_reference_whitespace(text: str, cursor: int) -> int:
    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1
    if cursor < len(text) and text[cursor] in "\r\n":
        if text[cursor] == "\r" and cursor + 1 < len(text) and text[cursor + 1] == "\n":
            cursor += 2
        else:
            cursor += 1
        while cursor < len(text) and text[cursor] in " \t":
            cursor += 1
    return cursor


def _reference_destination_end(text: str, start: int) -> tuple[str, int] | None:
    if start >= len(text):
        return None
    if text[start] == "<":
        cursor = start + 1
        while cursor < len(text) and text[cursor] not in "\r\n":
            if text[cursor] == "\\" and cursor + 1 < len(text):
                cursor += 2
                continue
            if text[cursor] == "<":
                return None
            if text[cursor] == ">":
                return text[start + 1 : cursor], cursor + 1
            cursor += 1
        return None

    cursor = start
    depth = 0
    while cursor < len(text):
        char = text[cursor]
        if char == "\\" and cursor + 1 < len(text) and text[cursor + 1] in MARKDOWN_PUNCTUATION:
            cursor += 2
            continue
        if char in " \t\r\n":
            break
        if ord(char) < 32 or ord(char) == 127 or char == "<":
            return None
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return None
            depth -= 1
        cursor += 1
    if cursor == start or depth:
        return None
    return text[start:cursor], cursor


def _reference_title_end(text: str, start: int, line_end: int) -> int | None:
    if start >= line_end or text[start] not in "\"'()":
        return None
    opener = text[start]
    closer = ")" if opener == "(" else opener
    depth = 1
    cursor = start + 1
    while cursor < line_end:
        char = text[cursor]
        if char == "\\" and cursor + 1 < line_end:
            cursor += 2
            continue
        if opener == "(" and char == "(":
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    return None


def _parse_reference_definition(
    text: str,
    start: int,
    line_end: int,
) -> tuple[str, str, int] | None:
    """Parse a one-line CommonMark link reference definition and optional title."""

    indent_end = start
    while indent_end < line_end and text[indent_end] == " ":
        indent_end += 1
    if indent_end - start > 3 or indent_end >= line_end or text[indent_end] != "[":
        return None
    label_end = _reference_label_end(text, indent_end, allow_nested=False)
    if label_end is None or label_end + 1 >= len(text) or text[label_end + 1] != ":":
        return None
    label = text[indent_end + 1 : label_end]
    normalized = _normalize_reference_label(label)
    if not normalized:
        return None

    cursor = _skip_reference_whitespace(text, label_end + 2)
    destination = _reference_destination_end(text, cursor)
    if destination is None:
        return None
    target, cursor = destination
    if cursor > line_end:
        return None

    title_cursor = cursor
    while title_cursor < line_end and text[title_cursor] in " \t":
        title_cursor += 1
    if title_cursor < line_end:
        title_end = _reference_title_end(text, title_cursor, line_end)
        if title_end is None or text[title_end:line_end].strip(" \t"):
            return None
        return normalized, target, title_end
    return normalized, target, cursor


def _reference_definition_boundary(lines: list[str], index: int, previous_was_definition: bool) -> bool:
    if index == 0 or previous_was_definition or not lines[index - 1].strip():
        return True
    previous = lines[index - 1]
    return bool(
        ATX_HEADING_RE.match(previous)
        or SETEXT_HEADING_RE.fullmatch(previous.strip())
        or is_thematic_break(previous)
    )


def _reference_definitions(text: str) -> tuple[dict[str, str], list[tuple[int, int]]]:
    lines = text.splitlines(keepends=True)
    definitions: dict[str, str] = {}
    spans: list[tuple[int, int]] = []
    offsets: list[tuple[int, int, str]] = []
    offset = 0
    for raw_line in lines:
        line_end = offset + len(raw_line.rstrip("\r\n"))
        offsets.append((offset, line_end, raw_line.rstrip("\r\n")))
        offset += len(raw_line)
    line_texts = [item[2] for item in offsets]

    index = 0
    previous_was_definition = False
    while index < len(offsets):
        start, line_end, line = offsets[index]
        if (
            REFERENCE_DEFINITION_OPEN_RE.match(line)
            and _reference_definition_boundary(line_texts, index, previous_was_definition)
        ):
            parsed = _parse_reference_definition(text, start, line_end)
            if parsed is not None:
                label, target, end = parsed
                definitions.setdefault(label, target)
                spans.append((start, end))
                while index + 1 < len(offsets) and offsets[index + 1][0] < end:
                    index += 1
                previous_was_definition = True
                index += 1
                continue
        previous_was_definition = False
        index += 1
    return definitions, spans


def _reference_link_targets(text: str) -> list[str]:
    """Return destinations of used full, collapsed, and shortcut references."""

    text = _mask_yaml_frontmatter(text)
    definitions, definition_spans = _reference_definitions(text)
    if not definitions:
        return []

    characters = list(text)
    spans = list(definition_spans)
    spans.extend((match.start(), match.end()) for match in MARKDOWN_LINK_RE.finditer(text))
    spans.extend((match.start(), match.end()) for match in MARKDOWN_IMAGE_RE.finditer(text))
    spans.extend((match.start(), match.end()) for match in WIKI_LINK_CHECK_RE.finditer(text))
    for start, end in spans:
        for position in range(start, end):
            if characters[position] not in "\r\n":
                characters[position] = " "
    visible = "".join(characters)

    targets: list[str] = []
    cursor = 0
    while cursor < len(visible):
        start = visible.find("[", cursor)
        if start < 0:
            break
        if _is_markdown_escape(visible, start) or (start and visible[start - 1] == "!"):
            cursor = start + 1
            continue
        label_end = _reference_label_end(visible, start, allow_nested=True)
        if label_end is None:
            cursor = start + 1
            continue
        first_label = visible[start + 1 : label_end]
        first_key = _normalize_reference_label(first_label)
        after_first = label_end + 1
        if after_first < len(visible) and visible[after_first] == "(":
            cursor = after_first + 1
            continue

        if after_first < len(visible) and visible[after_first] == "[":
            second_end = _reference_label_end(visible, after_first, allow_nested=False)
            if second_end is None:
                cursor = after_first + 1
                continue
            second_label = visible[after_first + 1 : second_end]
            reference_key = _normalize_reference_label(second_label) or first_key
            target = definitions.get(reference_key)
            if target is not None:
                targets.append(target)
                cursor = second_end + 1
            else:
                # The first label cannot be a shortcut when followed by another
                # label, but that second label may start a later full reference.
                cursor = after_first + 1
            continue

        target = definitions.get(first_key)
        if target is not None:
            targets.append(target)
            cursor = after_first
        else:
            cursor = start + 1
    return targets


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class LinkIssue:
    source: Path
    target: str
    kind: str


class LinkRootError(ValueError):
    """Stable public error for an invalid vault root."""

    REASON = "root must be an existing directory without symlink components"

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"{path}: {self.REASON}")


def is_external(target: str) -> bool:
    stripped = target.strip()
    return (
        not stripped
        or stripped.startswith("//")
        or bool(URI_SCHEME_RE.match(stripped))
    )


def _unescaped_marker_index(value: str) -> int | None:
    for index, char in enumerate(value):
        if char in "?#":
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and value[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                return index
    return None


def _clean_target_parts(target: str) -> tuple[str | None, str | None]:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if is_external(target):
        return None, None

    suffix_index = _unescaped_marker_index(target)
    path_part = target if suffix_index is None else target[:suffix_index]
    fragment_part = None
    if suffix_index is not None:
        marker = target[suffix_index]
        if marker == "#":
            fragment_part = target[suffix_index + 1 :]
        else:
            fragment_index = _unescaped_marker_index(target[suffix_index + 1 :])
            if fragment_index is not None and target[suffix_index + 1 + fragment_index] == "#":
                fragment_part = target[suffix_index + 2 + fragment_index :]

    path_part = unquote(unescape_markdown_destination(path_part)).strip()
    if fragment_part is not None:
        fragment_part = unquote(unescape_markdown_destination(fragment_part)).strip()
    if not path_part and not fragment_part:
        return None, None
    return path_part, fragment_part


def clean_target(target: str) -> str | None:
    path, _fragment = _clean_target_parts(target)
    return path


def _slugify_heading(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = re.sub(r"[^\w\s-]", "", normalized, flags=re.UNICODE)
    return re.sub(r"[-\s]+", "-", normalized).strip("-")


def _anchor_keys(value: str) -> set[str]:
    normalized = unquote(unescape_markdown_destination(value)).strip()
    if not normalized:
        return set()
    keys = {normalized.casefold()}
    slug = _slugify_heading(normalized)
    if slug:
        keys.add(slug)
    return keys


def _heading_anchor_keys(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    resolved = path.resolve()
    if resolved in cache:
        return cache[resolved]

    try:
        text = _mask_comment_spans(
            text_without_code(_mask_yaml_frontmatter(path.read_text(encoding="utf-8", errors="replace")))
        )
    except OSError:
        cache[resolved] = set()
        return cache[resolved]

    keys: set[str] = set()
    duplicate_counts: dict[str, int] = {}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = ATX_HEADING_RE.match(line)
        title = match.group("title") if match else None
        if title is None and index and SETEXT_HEADING_RE.fullmatch(line):
            previous = lines[index - 1].strip()
            if previous:
                title = previous
        if title is None:
            continue
        title = re.sub(r"[ \t]+#+[ \t]*$", "", title).strip()
        if not title:
            continue
        explicit_id = EXPLICIT_HEADING_ID_RE.search(title)
        if explicit_id:
            keys.update(_anchor_keys(explicit_id.group(1)))
            title = title[: explicit_id.start()].rstrip()
        keys.update(_anchor_keys(title))
        slug = _slugify_heading(title)
        if slug:
            count = duplicate_counts.get(slug, 0)
            if count:
                keys.add(f"{slug}-{count}")
            duplicate_counts[slug] = count + 1

    cache[resolved] = keys
    return keys


def _heading_hierarchy(
    path: Path,
    cache: dict[Path, list[tuple[frozenset[str], ...]]],
) -> list[tuple[frozenset[str], ...]]:
    """Return each heading's ordered ancestor chain and its anchor spellings."""

    resolved = path.resolve()
    if resolved in cache:
        return cache[resolved]

    try:
        text = _mask_comment_spans(
            text_without_code(_mask_yaml_frontmatter(path.read_text(encoding="utf-8", errors="replace")))
        )
    except OSError:
        cache[resolved] = []
        return cache[resolved]

    lines = text.splitlines()
    stack: dict[int, frozenset[str]] = {}
    duplicate_counts: dict[str, int] = {}
    paths: list[tuple[frozenset[str], ...]] = []
    for index, line in enumerate(lines):
        match = ATX_HEADING_RE.match(line)
        if match is not None:
            marker = re.match(r"^[ \t]*(#{1,6})", line)
            assert marker is not None
            level = len(marker.group(1))
            title = match.group("title")
        elif index and SETEXT_HEADING_RE.fullmatch(line):
            previous = lines[index - 1].strip()
            if not previous:
                continue
            marker = SETEXT_HEADING_RE.fullmatch(line)
            assert marker is not None
            level = 1 if marker.group("marker").startswith("=") else 2
            title = previous
        else:
            continue

        title = re.sub(r"[ \t]+#+[ \t]*$", "", title).strip()
        if not title:
            continue
        explicit_id = EXPLICIT_HEADING_ID_RE.search(title)
        if explicit_id:
            title = title[: explicit_id.start()].rstrip()
            keys = _anchor_keys(title)
            keys.update(_anchor_keys(explicit_id.group(1)))
        else:
            keys = _anchor_keys(title)
        slug = _slugify_heading(title)
        if slug:
            count = duplicate_counts.get(slug, 0)
            if count:
                keys.add(f"{slug}-{count}")
            duplicate_counts[slug] = count + 1

        stack = {ancestor: anchors for ancestor, anchors in stack.items() if ancestor < level}
        stack[level] = frozenset(keys)
        paths.append(tuple(stack[ancestor] for ancestor in sorted(stack)))

    cache[resolved] = paths
    return paths


def _has_heading_anchor(
    path: Path,
    fragment: str,
    cache: dict[Path, set[str]],
    hierarchy_cache: dict[Path, list[tuple[frozenset[str], ...]]] | None = None,
) -> bool:
    fragments = [part.strip() for part in fragment.split("#") if part.strip()]
    if not fragments:
        return False
    choices = [_anchor_keys(part) for part in fragments]
    if len(choices) == 1:
        return bool(choices[0] & _heading_anchor_keys(path, cache))

    paths = _heading_hierarchy(path, hierarchy_cache if hierarchy_cache is not None else {})
    for chain in paths:
        if len(chain) < len(choices):
            continue
        for offset in range(len(chain) - len(choices) + 1):
            if all(choices[index] & chain[offset + index] for index in range(len(choices))):
                return True
    return False


def _block_anchor_ids(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    resolved = path.resolve()
    if resolved in cache:
        return cache[resolved]
    try:
        text = _mask_comment_spans(
            text_without_code(_mask_yaml_frontmatter(path.read_text(encoding="utf-8", errors="replace")))
        )
    except OSError:
        cache[resolved] = set()
        return cache[resolved]

    identifiers = {
        match.group(1)
        for line in text.splitlines()
        if (match := BLOCK_ID_LINE_RE.search(line)) is not None
    }
    cache[resolved] = identifiers
    return identifiers


def _has_anchor(
    path: Path,
    fragment: str,
    heading_cache: dict[Path, set[str]],
    hierarchy_cache: dict[Path, list[tuple[frozenset[str], ...]]],
    block_cache: dict[Path, set[str]],
) -> bool:
    if path.suffix.lower() != ".md":
        # Obsidian uses format-specific fragments for PDFs, images, and other
        # attachments. A heading parser cannot validate those fragments.
        return True
    if fragment.startswith("^"):
        match = re.fullmatch(r"\^([A-Za-z0-9-]+)", fragment)
        return bool(match and match.group(1) in _block_anchor_ids(path, block_cache))
    return _has_heading_anchor(path, fragment, heading_cache, hierarchy_cache)


def build_stem_index(files: list[Path]) -> dict[str, list[Path]]:
    by_stem: dict[str, list[Path]] = {}
    for path in files:
        by_stem.setdefault(path.stem, []).append(path)
    return by_stem


def is_within_root(root: Path, candidate: Path) -> bool:
    """Return whether a resolved candidate stays inside the vault root."""

    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def is_regular_file_without_symlink_components(root: Path, path: Path) -> bool:
    """Require an in-root regular file reached without any symlink component."""

    root = root.resolve()
    try:
        root_mode = root.lstat().st_mode
        relative = path.relative_to(root)
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        return False

    current = root
    try:
        for index, component in enumerate(relative.parts):
            current = current / component
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                return False
            if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
                return False
        return bool(relative.parts) and stat.S_ISREG(mode)
    except OSError:
        return False


def resolve_target(
    root: Path,
    source: Path,
    raw_target: str,
    by_stem: dict[str, list[Path]],
    *,
    allow_vault_fallback: bool = True,
) -> list[Path]:
    target, _fragment = _clean_target_parts(raw_target)
    if target is None:
        return []

    root_relative = target.startswith("/")
    if root_relative:
        target = target.lstrip("/")

    root = root.resolve()
    candidates = []
    if not target and not root_relative:
        candidates.append(source)
    else:
        if root_relative:
            bases = (root,)
        elif allow_vault_fallback:
            bases = (source.parent, root)
        else:
            bases = (source.parent,)
        for base in bases:
            candidate = base / target
            if is_within_root(root, candidate):
                candidates.append(candidate)
            if target and not target.endswith(".md"):
                candidate = base / f"{target}.md"
                if is_within_root(root, candidate):
                    candidates.append(candidate)

    if allow_vault_fallback and not root_relative and "/" not in target and target in by_stem:
        candidates.extend(
            candidate
            for candidate in by_stem[target]
            if is_within_root(root, candidate)
        )

    resolved = []
    for candidate in candidates:
        resolved_candidate = candidate.resolve()
        if (
            is_regular_file_without_symlink_components(root, candidate)
            and resolved_candidate not in resolved
        ):
            resolved.append(resolved_candidate)
    return resolved


def check_links(
    root: Path,
    *,
    files: list[Path] | None = None,
    include_markdown: bool = True,
    include_wiki: bool = True,
) -> tuple[list[LinkIssue], list[LinkIssue], int]:
    try:
        root = ensure_safe_input_directory(root)
    except (OSError, ValueError):
        raise LinkRootError(root) from None
    markdown_files: list[Path] = []
    boundary_issues: list[LinkIssue] = []
    if files is None:
        for path in sorted(path for path in root.rglob("*") if ".git" not in path.parts):
            if path.is_symlink() and not is_within_root(root, path):
                boundary_issues.append(LinkIssue(path, str(path.resolve()), "outside_root"))
                continue
            if not path.is_file() or path.suffix.lower() != ".md":
                continue
            markdown_files.append(path)
    else:
        selected: set[Path] = set()
        for value in files:
            path = Path(value)
            if not path.is_absolute():
                path = root / path
            path = path.absolute()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"selected Markdown file is outside root: {path}") from exc
            if path.suffix.lower() != ".md" or not is_regular_file_without_symlink_components(root, path):
                raise ValueError(f"selected Markdown file is not a regular in-root file: {path}")
            selected.add(path)
        markdown_files = sorted(selected)
    by_stem = build_stem_index(markdown_files)
    allowed_markdown_targets = (
        {path.resolve() for path in markdown_files} if files is not None else None
    )
    broken: list[LinkIssue] = boundary_issues
    self_links: list[LinkIssue] = []
    checked = 0
    anchor_cache: dict[Path, set[str]] = {}
    hierarchy_cache: dict[Path, list[tuple[frozenset[str], ...]]] = {}
    block_cache: dict[Path, set[str]] = {}

    patterns = []
    if include_markdown:
        patterns.append((MARKDOWN_LINK_RE, False, "markdown"))
    if include_wiki:
        patterns.append((WIKI_LINK_CHECK_RE, True, "wiki"))

    def check_target(source: Path, target: str, allow_vault_fallback: bool) -> None:
        nonlocal checked
        cleaned_path, fragment = _clean_target_parts(target)
        if cleaned_path is None:
            return
        checked += 1
        hits = resolve_target(
            root,
            source,
            target,
            by_stem,
            allow_vault_fallback=allow_vault_fallback,
        )
        if allowed_markdown_targets is not None:
            hits = [
                hit
                for hit in hits
                if hit.suffix.lower() != ".md" or hit in allowed_markdown_targets
            ]
        if fragment is not None:
            hits = [
                hit
                for hit in hits
                if _has_anchor(
                    hit,
                    fragment,
                    anchor_cache,
                    hierarchy_cache,
                    block_cache,
                )
            ]
        if not hits:
            broken.append(LinkIssue(source, target, "broken"))
        elif cleaned_path and hits[0] == source.resolve():
            self_links.append(LinkIssue(source, target, "self"))

    for source in markdown_files:
        text = _mask_comment_spans(
            text_without_code(source.read_text(encoding="utf-8", errors="replace"))
        )
        for regex, allow_vault_fallback, link_kind in patterns:
            for match in regex.finditer(text):
                if link_kind == "wiki" and _is_escaped(text, match.start()):
                    continue
                target = match.group(1)
                if link_kind == "wiki" and _is_wiki_search_target(target):
                    checked += 1
                    continue
                check_target(source, target, allow_vault_fallback)
        if include_markdown:
            for target in _reference_link_targets(text):
                check_target(source, target, False)

    return broken, self_links, checked


def check_markdown_links(
    root: Path,
    *,
    files: list[Path] | None = None,
) -> tuple[list[LinkIssue], list[LinkIssue], int]:
    """Check Markdown inline and supported reference destinations in a safe file set."""

    return check_links(
        root,
        files=files,
        include_markdown=True,
        include_wiki=False,
    )


def print_issue(root: Path, issue: LinkIssue) -> None:
    source = issue.source.relative_to(root)
    print(f"{issue.kind.upper()}: {source} -> {issue.target}")


def main() -> int:
    configure_output_encoding()
    parser = argparse.ArgumentParser(description="Check Obsidian Markdown links.")
    parser.add_argument("root", type=Path, help="Vault or notes directory")
    parser.add_argument("--allow-self-links", action="store_true")
    args = parser.parse_args()

    try:
        root = ensure_safe_input_directory(args.root)
    except (OSError, ValueError):
        print(f"ERROR: {LinkRootError(args.root)}", file=sys.stderr)
        return 2

    broken, self_links, checked = check_links(root)
    print(f"checked_links {checked}")
    print(f"broken_links {len(broken)}")
    print(f"self_links {len(self_links)}")

    for issue in broken:
        print_issue(root, issue)
    if not args.allow_self_links:
        for issue in self_links:
            print_issue(root, issue)

    if broken or (self_links and not args.allow_self_links):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
