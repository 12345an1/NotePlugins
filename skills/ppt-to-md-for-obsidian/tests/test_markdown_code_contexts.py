from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.check_course_notes import find_course_note_issues, find_table_issues
from scripts.check_obsidian_links import check_links, text_without_code


@dataclass(frozen=True)
class ContextCase:
    name: str
    before: tuple[str, ...]
    prefix: str
    after: tuple[str, ...]
    is_code: bool


@dataclass(frozen=True)
class EmptyListPaddingCase:
    name: str
    marker: str
    trailing_whitespace: str


CASES = (
    ContextCase("top_space_code", (), "    ", (), True),
    ContextCase("top_tab_code", (), "\t", (), True),
    ContextCase("top_fence", ("```text",), "", ("````",), True),
    ContextCase("bullet_space_continuation", ("- body",), "    ", (), False),
    ContextCase("ordered_space_continuation", ("1. body",), "     ", (), False),
    ContextCase("bullet_tab_continuation", ("- body",), "\t", (), False),
    ContextCase("empty_dash_continuation", ("-",), "    ", (), False),
    ContextCase("empty_star_tab_continuation", ("*",), "\t", (), False),
    ContextCase("nested_bullet_continuation", ("- outer", "  - inner"), "      ", (), False),
    ContextCase("nested_ordered_continuation", ("- outer", "  1. inner"), "       ", (), False),
    ContextCase("list_blank_line_indented_code", ("- body", ""), "      ", (), True),
    ContextCase(
        "nested_list_fence",
        ("- outer", "  - inner", "    ```text"),
        "    ",
        ("    ````",),
        True,
    ),
    ContextCase("top_unclosed_fence", ("```text",), "", (), True),
    ContextCase(
        "list_unclosed_fence_outdent",
        ("- item", "  ```text", "  code"),
        "",
        (),
        False,
    ),
    ContextCase(
        "nested_list_unclosed_fence_outdent",
        ("- outer", "  - inner", "    ```text", "    code"),
        "  ",
        (),
        False,
    ),
    ContextCase(
        "list_unclosed_fence_empty_line_then_code",
        ("- item", "  ```text", ""),
        "  ",
        (),
        True,
    ),
    ContextCase(
        "list_unclosed_fence_space_only_line_then_code",
        ("- item", "  ```text", "   "),
        "  ",
        (),
        True,
    ),
    ContextCase("blockquote_body", (), "> ", (), False),
    ContextCase("blockquote_indented_code", (), ">     ", (), True),
    ContextCase("blockquote_fence", ("> ```text",), "> ", ("> ```",), True),
    ContextCase("dash_thematic_break_then_code", ("---", ""), "    ", (), True),
    ContextCase("star_thematic_break_then_code", ("* * *", ""), "    ", (), True),
    ContextCase("inline_multiplication_then_code", ("a * b", ""), "    ", (), True),
)


EMPTY_LIST_PADDING_CASES = tuple(
    EmptyListPaddingCase(
        name=f"{marker_name}_spaces_{space_count}",
        marker=marker,
        trailing_whitespace=" " * space_count,
    )
    for marker_name, marker in (
        ("dash", "-"),
        ("star", "*"),
        ("plus", "+"),
        ("ordered_dot", "1."),
        ("ordered_paren", "1)"),
    )
    for space_count in range(6)
) + tuple(
    EmptyListPaddingCase(
        name=f"{marker_name}_{whitespace_name}",
        marker=marker,
        trailing_whitespace=trailing_whitespace,
    )
    for marker_name, marker in (
        ("dash", "-"),
        ("star", "*"),
        ("plus", "+"),
        ("ordered_dot", "1."),
        ("ordered_paren", "1)"),
    )
    for whitespace_name, trailing_whitespace in (
        ("tab", "\t"),
        ("space_tab", " \t"),
    )
)


def render_case(case: ContextCase, payload: str) -> str:
    payload_lines = payload.splitlines()
    lines = [*case.before, *(f"{case.prefix}{line}" for line in payload_lines), *case.after]
    return "\n".join(lines) + "\n"


def render_empty_list_padding_case(case: EmptyListPaddingCase, payload: str) -> str:
    payload_lines = "\n".join(f"    {line}" for line in payload.splitlines())
    return f"{case.marker}{case.trailing_whitespace}\n{payload_lines}\n"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_context_matrix_matches_markdown_it_commonmark_oracle(case: ContextCase) -> None:
    markdown_it = pytest.importorskip("markdown_it")
    text = render_case(case, "ORACLE_SENTINEL")
    payload_line = len(case.before)
    tokens = markdown_it.MarkdownIt("commonmark").parse(text)
    oracle_is_code = any(
        token.type in {"code_block", "fence"}
        and token.map is not None
        and token.map[0] <= payload_line < token.map[1]
        for token in tokens
    )

    assert oracle_is_code is case.is_code


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_course_residue_consumer_uses_commonmark_context(case: ContextCase, tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(render_case(case, "TODO mechanism explanation"), encoding="utf-8")

    issues = find_course_note_issues(tmp_path)
    residue_issues = [issue for issue in issues if issue.kind == "template_residue"]

    assert bool(residue_issues) is not case.is_code


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_link_consumer_uses_commonmark_context(case: ContextCase, tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(render_case(case, "[missing](missing.md)"), encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == (0 if case.is_code else 1)
    assert bool(broken) is not case.is_code
    assert self_links == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_table_consumer_uses_commonmark_context(case: ContextCase, tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    table = "| A | B |\n|---|---|\n| 1 | 2 | 3 |"
    text = text_without_code(render_case(case, table))

    issues = find_table_issues(tmp_path, note, text)

    assert bool(issues) is not case.is_code


def test_markdown_it_gfm_table_extension_accepts_table_inside_blockquote() -> None:
    markdown_it = pytest.importorskip("markdown_it")
    parser = markdown_it.MarkdownIt("commonmark").enable("table")
    tokens = parser.parse("> | A | B |\n> |---|---|\n> | 1 | 2 |\n")

    assert any(token.type == "table_open" for token in tokens)


@pytest.mark.parametrize(
    "text, expected_checked",
    (
        ("`code\n[missing](missing.md)\n`\n", 0),
        ("``code `\n[missing](missing.md)\n``\n", 0),
        ("`unclosed\n\n[missing](missing.md)\n`\n", 1),
        ("`unclosed\n# Heading\n[missing](missing.md)\n`\n", 1),
        ("- `unclosed\n- [missing](missing.md) `\n", 1),
    ),
)
def test_multiline_inline_code_matches_commonmark_link_visibility(
    text: str,
    expected_checked: int,
    tmp_path: Path,
) -> None:
    markdown_it = pytest.importorskip("markdown_it")
    tokens = markdown_it.MarkdownIt("commonmark").parse(text)
    oracle_links = sum(
        child.type == "link_open"
        for token in tokens
        for child in (token.children or [])
    )
    note = tmp_path / "note.md"
    note.write_text(text, encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert oracle_links == expected_checked
    assert checked == expected_checked
    assert len(broken) == expected_checked
    assert self_links == []


@pytest.mark.parametrize(
    "line_ending",
    ("\n", "\r\n", "\r"),
    ids=("lf", "crlf", "cr"),
)
def test_inline_code_soft_and_blank_line_boundaries_cover_all_line_endings(
    line_ending: str,
    tmp_path: Path,
) -> None:
    soft = tmp_path / "soft.md"
    soft.write_text(
        f"`code{line_ending}[hidden](missing.md)`{line_ending}",
        encoding="utf-8",
        newline="",
    )
    broken, self_links, checked = check_links(tmp_path)
    assert checked == 0
    assert broken == []
    assert self_links == []

    soft.unlink()
    blank = tmp_path / "blank.md"
    blank.write_text(
        f"`unclosed{line_ending}{line_ending}[visible](missing.md)`{line_ending}",
        encoding="utf-8",
        newline="",
    )
    broken, self_links, checked = check_links(tmp_path)
    assert checked == 1
    assert [issue.target for issue in broken] == ["missing.md"]
    assert self_links == []


@pytest.mark.parametrize("case", EMPTY_LIST_PADDING_CASES, ids=lambda case: case.name)
def test_empty_list_padding_matches_markdown_it_commonmark_oracle(case: EmptyListPaddingCase) -> None:
    markdown_it = pytest.importorskip("markdown_it")
    text = render_empty_list_padding_case(case, "ORACLE_SENTINEL")
    tokens = markdown_it.MarkdownIt("commonmark").parse(text)
    oracle_is_code = any(
        token.type in {"code_block", "fence"}
        and token.map is not None
        and token.map[0] <= 1 < token.map[1]
        for token in tokens
    )

    assert not oracle_is_code


@pytest.mark.parametrize("case", EMPTY_LIST_PADDING_CASES, ids=lambda case: case.name)
def test_empty_list_padding_is_visible_to_course_residue_consumer(
    case: EmptyListPaddingCase,
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        render_empty_list_padding_case(case, "TODO mechanism explanation"),
        encoding="utf-8",
    )

    issues = find_course_note_issues(tmp_path)

    assert any(issue.kind == "template_residue" for issue in issues)


@pytest.mark.parametrize("case", EMPTY_LIST_PADDING_CASES, ids=lambda case: case.name)
def test_empty_list_padding_is_visible_to_link_consumer(
    case: EmptyListPaddingCase,
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        render_empty_list_padding_case(case, "[missing](missing.md)"),
        encoding="utf-8",
    )

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 1
    assert len(broken) == 1
    assert self_links == []


@pytest.mark.parametrize("case", EMPTY_LIST_PADDING_CASES, ids=lambda case: case.name)
def test_empty_list_padding_is_visible_to_table_consumer(
    case: EmptyListPaddingCase,
    tmp_path: Path,
) -> None:
    note = tmp_path / "note.md"
    table = "| A | B |\n|---|---|\n| 1 | 2 | 3 |"
    text = text_without_code(render_empty_list_padding_case(case, table))

    issues = find_table_issues(tmp_path, note, text)

    assert issues


@pytest.mark.parametrize("marker", ("-", "*", "+", "1.", "1)"))
def test_four_space_padding_with_same_line_body_remains_visible(marker: str) -> None:
    text = f"{marker}    TODO [x](missing.md)\n"

    assert "TODO [x](missing.md)" in text_without_code(text)
