from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from scripts.check_course_notes import find_course_note_issues, linked_note_stems
from scripts.safe_io import InputRootError


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_course_notes.py"
SUBPROCESS_TIMEOUT_SECONDS = 60


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("kind", ("missing", "file", "symlink-dir"))
def test_find_course_note_issues_api_rejects_invalid_root_shape(
    tmp_path: Path,
    kind: str,
) -> None:
    regular = tmp_path / "regular"
    write(regular / "note.md", "# Note\n")
    if kind == "missing":
        root = tmp_path / "missing"
    elif kind == "file":
        root = tmp_path / "root.md"
        write(root, "# File\n")
    else:
        root = tmp_path / "alias"
        root.symlink_to(regular, target_is_directory=True)

    with pytest.raises(InputRootError) as caught:
        find_course_note_issues(root)

    assert str(caught.value) == (
        f"{root}: root must be an existing directory without symlink components"
    )


def write_minimal_course(root: Path, table: str) -> None:
    write(
        root / "00_课程总览.md",
        "# 课程总览\n\n[[知识点详细版_含公式]]\n[[知识点精简复习版_含公式]]\n",
    )
    write(root / "知识点详细版_含公式.md", "# 详细版\n\n内容。\n")
    write(root / "知识点精简复习版_含公式.md", "# 精简版\n\n内容。\n")
    write(root / "01_导论.md", "# 导论\n\n" + table + "\n")


def run_checker(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, str(root)],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )


def write_review_course(root: Path, overview: str) -> None:
    write(root / "00_课程总览.md", overview)
    write(root / "知识点详细版_含公式.md", "# 详细版\n\n内容。\n")
    write(root / "知识点精简复习版_含公式.md", "# 精简版\n\n内容。\n")


def test_review_link_api_requires_real_targets_outside_code(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    overview = (
        "# 课程总览\n\n"
        "纯文本提到知识点详细版_含公式和知识点精简复习版_含公式。\n\n"
        "```markdown\n"
        "[[知识点详细版_含公式]]\n"
        "[精简](知识点精简复习版_含公式.md)\n"
        "```\n"
    )
    write_review_course(course, overview)

    assert linked_note_stems(overview) == set()
    issues = find_course_note_issues(course)
    assert sum(issue.kind == "missing_review_link" for issue in issues) == 2


def test_review_link_cli_accepts_real_wiki_and_markdown_targets(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_review_course(
        course,
        "# 课程总览\n\n"
        "纯文本：知识点详细版_含公式、知识点精简复习版_含公式。\n",
    )

    missing = run_checker(course)
    assert missing.returncode == 1
    assert missing.stdout.count("MISSING_REVIEW_LINK") == 2

    write(
        course / "00_课程总览.md",
        "# 课程总览\n\n"
        "- [[知识点详细版_含公式|详细复习]]\n"
        "- [精简复习](知识点精简复习版_含公式.md)\n",
    )
    linked = run_checker(course)
    assert linked.returncode == 0, linked.stdout + linked.stderr
    assert "course_note_issues 0" in linked.stdout


def test_check_course_notes_reports_broken_markdown_table(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_minimal_course(
        course,
        "\n".join(
            [
                "| 正则式 | 语言 |",
                "|---|---|",
                "| a|b | {a,b} |",
            ]
        ),
    )

    result = run_checker(course)

    assert result.returncode == 1
    assert "BROKEN_TABLE" in result.stdout


def test_check_course_notes_accepts_escaped_pipes_and_plain_wiki_links(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_minimal_course(
        course,
        "\n".join(
            [
                "| 正则式 | 对应笔记 |",
                "|---|---|",
                "| `a\\|b` | [[课程/01_导论]] |",
            ]
        ),
    )

    result = run_checker(course)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "course_note_issues 0" in result.stdout


def test_check_course_notes_masks_inline_fenced_and_indented_code(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_minimal_course(
        course,
        "\n".join(
            [
                "| 公式 | 含义 |",
                "|---|---|",
                "| `P(s_{t+1}|s_t,a_t)` | 转移概率 |",
                "",
                "路径示意：`anaconda3/envs/<环境名>/.../site-packages`。",
                "",
                "```python",
                "TODO = '... | ...'",
                "```",
                "",
                "    indented = 'TODO ... | ...'",
            ]
        ),
    )

    result = run_checker(
        course,
        "--strict-depth",
        "--min-chapter-lines",
        "1",
        "--min-detailed-lines",
        "1",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "course_note_issues 0" in result.stdout


def test_check_course_notes_reports_unclosed_tilde_fence(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_minimal_course(course, "~~~python\nprint('unterminated')")

    result = run_checker(course)

    assert result.returncode == 1
    assert "UNBALANCED_FENCE" in result.stdout


@pytest.mark.parametrize(
    "body",
    [
        "```text\n$$\n```",
        "Inline code: `$$`.",
        "    $$",
        "~~~text\n$$\n~~~",
        "> ~~~text\n> $$\n> ~~~",
        "- item\n  ~~~text\n  $$\n  ~~~",
    ],
    ids=("backtick-fence", "inline", "indented", "tilde-fence", "blockquote-fence", "list-fence"),
)
def test_check_course_notes_ignores_math_markers_in_commonmark_code(
    tmp_path: Path,
    body: str,
) -> None:
    course = tmp_path / "课程"
    write_minimal_course(course, body)

    result = run_checker(course)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNBALANCED_MATH" not in result.stdout


def test_check_course_notes_still_reports_unbalanced_math_outside_code(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_minimal_course(course, "$$\nx + y")

    result = run_checker(course)

    assert result.returncode == 1
    assert "UNBALANCED_MATH" in result.stdout


@pytest.mark.parametrize(
    "body",
    [
        "<!--\n$$\n-->",
        "%%\n$$\n%%",
        r"\$\$",
        "The literal token $$ appears in this prose sentence.",
    ],
    ids=("html-comment", "obsidian-comment", "escaped-literal", "prose"),
)
def test_check_course_notes_ignores_non_delimiter_math_text(tmp_path: Path, body: str) -> None:
    course = tmp_path / "课程"
    write_minimal_course(course, body)

    result = run_checker(course)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNBALANCED_MATH" not in result.stdout


@pytest.mark.parametrize("body", ["> $$\n> x + y", "- item\n  $$\n  x + y"], ids=("blockquote", "list"))
def test_check_course_notes_counts_unbalanced_math_in_containers(tmp_path: Path, body: str) -> None:
    course = tmp_path / "课程"
    write_minimal_course(course, body)

    result = run_checker(course)

    assert result.returncode == 1
    assert "UNBALANCED_MATH" in result.stdout


def test_check_course_notes_reports_template_residue_in_list_continuation(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_minimal_course(
        course,
        "- 尚未完成的正文条目：\n    TODO 后续补全机制解释。",
    )

    result = run_checker(
        course,
        "--strict-depth",
        "--min-chapter-lines",
        "1",
        "--min-detailed-lines",
        "1",
    )

    assert result.returncode == 1
    assert "TEMPLATE_RESIDUE" in result.stdout


def test_check_course_notes_still_masks_top_level_indented_code(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_minimal_course(course, "    TODO = 'code placeholder'")

    result = run_checker(
        course,
        "--strict-depth",
        "--min-chapter-lines",
        "1",
        "--min-detailed-lines",
        "1",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "course_note_issues 0" in result.stdout


def test_check_course_notes_keeps_body_template_residue_visible(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_minimal_course(course, "正文仍然包含 ... 占位内容。")

    result = run_checker(
        course,
        "--strict-depth",
        "--min-chapter-lines",
        "1",
        "--min-detailed-lines",
        "1",
    )

    assert result.returncode == 1
    assert "GENERIC_TEMPLATE_RESIDUE" in result.stdout


def test_check_course_notes_skip_dir_ignores_non_course_index_dirs(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_minimal_course(course, "正文。")
    write(course / "概念索引" / "index.md", "# Index\n\nTODO\n")
    write(course / "生成审查" / "empty.md", "")

    without_skip = run_checker(course)
    assert without_skip.returncode == 1
    assert "TEMPLATE_RESIDUE" in without_skip.stdout
    assert "EMPTY_FILE" in without_skip.stdout

    with_skip = run_checker(course, "--skip-dir", "概念索引", "--skip-dir", "生成审查")
    assert with_skip.returncode == 0, with_skip.stdout + with_skip.stderr
    assert "course_note_issues 0" in with_skip.stdout


@pytest.mark.parametrize(
    "required_name",
    ["00_课程总览.md", "知识点详细版_含公式.md", "知识点精简复习版_含公式.md"],
)
def test_check_course_notes_rejects_external_required_file_symlink(
    tmp_path: Path,
    required_name: str,
) -> None:
    course = tmp_path / "课程"
    write_minimal_course(course, "正文。")
    required = course / required_name
    content = required.read_text(encoding="utf-8")
    required.unlink()
    outside = tmp_path / required_name
    write(outside, content)
    required.symlink_to(outside)

    result = run_checker(course)

    assert result.returncode == 1
    assert "UNSAFE_SYMLINK" in result.stdout


def test_check_course_notes_rejects_external_directory_symlink(tmp_path: Path) -> None:
    course = tmp_path / "课程"
    write_minimal_course(course, "正文。")
    outside = tmp_path / "outside"
    write(outside / "external.md", "# External\n")
    (course / "linked").symlink_to(outside, target_is_directory=True)

    result = run_checker(course)

    assert result.returncode == 1
    assert "UNSAFE_SYMLINK" in result.stdout
