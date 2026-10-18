from pathlib import Path

from scripts.dedupe_headings import (
    find_duplicate_headings,
    main,
    normalize_heading,
    remove_duplicate_headings,
)


def test_removes_h2_echo_under_identical_h1():
    src = "# 经验风险与泛化\n\n## 经验风险与泛化\n\n正文。\n"
    out, removed = remove_duplicate_headings(src)
    assert out == "# 经验风险与泛化\n\n正文。\n"
    assert [item.title for item in removed] == ["经验风险与泛化"]


def test_removes_adjacent_identical_same_level_headings():
    src = "## 控制思路\n\n## 控制思路\n\n内容\n"
    out, removed = remove_duplicate_headings(src)
    assert out == "## 控制思路\n\n内容\n"
    assert len(removed) == 1


def test_collapses_heading_chain():
    src = "# 边界\n\n## 边界\n\n### 边界\n\n内容\n"
    out, removed = remove_duplicate_headings(src)
    assert out == "# 边界\n\n内容\n"
    assert len(removed) == 2


def test_keeps_same_title_separated_by_content():
    src = "## 边界\n\n正文一。\n\n## 边界\n\n正文二。\n"
    out, removed = remove_duplicate_headings(src)
    assert out == src
    assert removed == []


def test_keeps_deeper_then_shallower_repeat():
    src = "## 绪论\n\n# 绪论\n\n内容\n"
    out, removed = remove_duplicate_headings(src)
    assert removed == []


def test_ignores_headings_inside_fenced_code():
    src = "# 标题\n\n```markdown\n## 标题\n```\n\n## 标题\n\n正文\n"
    out, removed = remove_duplicate_headings(src)
    # The fenced copy is not a heading; the real duplicate is separated by the fence.
    assert removed == []


def test_ignores_frontmatter_and_its_comments():
    src = "---\ntitle: 经验风险与泛化\n# comment\ntags: [ml]\n---\n\n## 经验风险与泛化\n\n正文\n"
    out, removed = remove_duplicate_headings(src)
    assert removed == []


def test_normalizes_width_case_and_trailing_colon():
    src = "#  经验风险与泛化  \n\n## 经验风险与泛化：\n\n正文\n"
    out, removed = remove_duplicate_headings(src)
    assert len(removed) == 1


def test_normalize_heading_strips_closing_hashes_and_id():
    assert normalize_heading(" 经验风险 ##") == "经验风险"
    assert normalize_heading("经验风险 {#custom-id}") == "经验风险"


def test_no_duplicates_returns_original_text():
    src = "# 标题\n\n## 第一节\n\n正文\n\n## 第二节\n\n正文\n"
    out, removed = remove_duplicate_headings(src)
    assert out == src
    assert removed == []


def test_idempotent():
    src = "# 标题\n\n## 标题\n\n### 标题\n\n正文\n"
    once, _ = remove_duplicate_headings(src)
    twice, removed = remove_duplicate_headings(once)
    assert twice == once
    assert removed == []


def test_find_duplicate_headings_reports_line_numbers():
    src = "# 标题\n\n## 标题\n\n正文\n"
    duplicates = find_duplicate_headings(src)
    assert [(item.line_number, item.level) for item in duplicates] == [(3, 2)]


def test_cli_check_reports_without_writing(tmp_path: Path, capsys):
    note = tmp_path / "01_绪论.md"
    src = "# 绪论\n\n## 绪论\n\n正文\n"
    note.write_text(src, encoding="utf-8")

    code = main([str(tmp_path), "--check"])

    assert code == 1
    assert note.read_text(encoding="utf-8") == src
    assert "would remove" in capsys.readouterr().out


def test_cli_fixes_file_in_place(tmp_path: Path):
    note = tmp_path / "01_绪论.md"
    note.write_text("# 绪论\n\n## 绪论\n\n正文\n", encoding="utf-8")

    code = main([str(tmp_path)])

    assert code == 0
    assert note.read_text(encoding="utf-8") == "# 绪论\n\n正文\n"


def test_cli_does_not_touch_non_markdown(tmp_path: Path):
    note = tmp_path / "01_绪论.md"
    other = tmp_path / "data.txt"
    src = "# 绪论\n\n## 绪论\n\n正文\n"
    note.write_text(src, encoding="utf-8")
    other.write_text(src, encoding="utf-8")

    main([str(tmp_path)])

    assert other.read_text(encoding="utf-8") == src
