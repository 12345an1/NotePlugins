from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "check_source_coverage.py"
SUBPROCESS_TIMEOUT_SECONDS = 60
sys.path.insert(0, str(SKILL_ROOT))

from scripts.check_source_coverage import (  # noqa: E402
    SourceEntry,
    check_example_evidence,
    exact_regular_source_target,
    has_source_or_generated_example,
    page_source_evidence,
    resolve_beneath,
    resolve_note_target,
    source_boundary_issues,
    source_files,
    visible_source_references,
)


def test_generated_example_evidence_accepts_natural_and_legacy_wording() -> None:
    assert has_source_or_generated_example(
        "来源说明：自拟教学例：源课件未提供可独立还原的对应例题"
    )
    assert has_source_or_generated_example(
        "来源说明：自拟教学例；课件只给出背景，没有给出这组数值。"
    )
    assert has_source_or_generated_example(
        "来源说明：自拟教学例；课件只给出背景，没有给出重复 ID 案例。"
    )
    assert has_source_or_generated_example(
        "来源说明：生成：PPT/PDF 未提供独立可抽取例题"
    )
    assert not has_source_or_generated_example(
        "来源说明：这是自拟题，沿用课件背景，但答案不是唯一。"
    )
    assert not has_source_or_generated_example(
        "来源说明：这是自拟题，没有采用课件中的第二种解法。"
    )
    assert not has_source_or_generated_example(
        "来源说明：本题不是自拟题；课件没有给出答案。"
    )
    assert not has_source_or_generated_example(
        "来源说明：本题不是真正的自拟题；课件没有给出答案。"
    )
    assert not has_source_or_generated_example(
        "来源说明：本题不算自拟题；课件没有给出对应答案。"
    )


def test_plain_self_written_label_is_counted_as_generated(tmp_path: Path) -> None:
    assert has_source_or_generated_example(
        "来源说明：自拟教学例；背景见 `课程/数据.pptx` slide 27。"
    )
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "example.md").write_text(
        "## PPT/PDF 例题辅助理解\n\n"
        "| 知识点 | 例题与解析 | 来源 |\n|---|---|---|\n"
        "| 数据质量 | 按客户实体去重，再比较统计结果。 | "
        "自拟教学例；背景见 `课程/数据.pptx` slide 27。 |\n",
        encoding="utf-8",
    )
    _, _, sourced, generated, issues = check_example_evidence([notes])
    assert (sourced, generated, issues) == (0, 1, [])


def run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("header", "separator", "row"),
    (
        (
            "| 知识点 | 例题与解析 | 来源 |",
            "|---|---|---|",
            "| 图连通性 | 从首个顶点开始扫描边并标记相邻顶点；一轮无新增时停止。"
            "全部顶点均被标记，当且仅当图连通。 | `course/lecture.ppt` |",
        ),
        (
            "| 知识点 | 例题与解析 | 来源",
            "|---|---|---",
            "| 图连通性 | 从首个顶点开始扫描边并标记相邻顶点；一轮无新增时停止。"
            "全部顶点均被标记，当且仅当图连通。 | `course/lecture.ppt`",
        ),
        (
            "来源 | 知识点 | 例题与解析",
            "---|---|---",
            "`course/lecture.ppt` | 图连通性 | 从首个顶点开始扫描边并标记相邻顶点；"
            "一轮无新增时停止。全部顶点均被标记，当且仅当图连通。",
        ),
        (
            "来源 | 知识点 | 例题与解析 | 备注",
            "---|---|---|---",
            "`course/lecture.ppt` | 知识点 | 例题与解析 | 来源",
        ),
    ),
    ids=(
        "outer-pipes",
        "no-trailing-pipe",
        "reordered-no-outer-pipes",
        "header-like-data-row",
    ),
)
def test_example_table_source_file_is_a_complete_traceable_marker(
    tmp_path: Path,
    header: str,
    separator: str,
    row: str,
) -> None:
    notes_dir = tmp_path / "notes" / "course"
    write(
        notes_dir / "01_intro.md",
        "# Intro\n\n"
        "## PPT/PDF 例题辅助理解\n\n"
        f"{header}\n"
        f"{separator}\n"
        f"{row}\n",
    )

    _, _, source_examples, generated_examples, issues = check_example_evidence(
        [notes_dir]
    )

    assert source_examples == 1
    assert generated_examples == 0
    assert not issues


def make_root_shape(tmp_path: Path, label: str, kind: str) -> Path:
    regular_dir = tmp_path / f"{label}-regular-dir"
    regular_dir.mkdir()
    if kind == "regular-dir":
        return regular_dir
    if kind == "missing":
        return tmp_path / f"{label}-missing"
    regular_file = tmp_path / f"{label}-file"
    regular_file.write_text("fixture\n", encoding="utf-8")
    if kind == "file":
        return regular_file
    alias = tmp_path / f"{label}-{kind}"
    if kind == "symlink-dir":
        alias.symlink_to(regular_dir, target_is_directory=True)
    elif kind == "symlink-file":
        alias.symlink_to(regular_file)
    elif kind == "broken-symlink":
        alias.symlink_to(tmp_path / f"{label}-missing-target")
    elif kind == "ancestor-symlink":
        real_parent = tmp_path / f"{label}-real-parent"
        nested = real_parent / "nested"
        nested.mkdir(parents=True)
        linked_parent = tmp_path / f"{label}-linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        return linked_parent / "nested"
    else:
        raise AssertionError(kind)
    return alias


@pytest.mark.parametrize("option", ("--source-root", "--notes-root"))
@pytest.mark.parametrize(
    "kind",
    ("missing", "file", "symlink-dir", "symlink-file", "broken-symlink", "ancestor-symlink"),
)
@pytest.mark.parametrize("strict", (False, True), ids=("mapped", "strict"))
def test_source_coverage_cli_rejects_invalid_root_shapes_before_scanning(
    tmp_path: Path,
    option: str,
    kind: str,
    strict: bool,
) -> None:
    source_root = make_root_shape(tmp_path, "source", "regular-dir")
    notes_root = make_root_shape(tmp_path, "notes", "regular-dir")
    invalid = make_root_shape(tmp_path, option.removeprefix("--"), kind)
    roots = {"--source-root": source_root, "--notes-root": notes_root}
    roots[option] = invalid
    arguments = [
        "--source-root",
        str(roots["--source-root"]),
        "--notes-root",
        str(roots["--notes-root"]),
    ]
    arguments += ["--strict"] if strict else ["--mapping", "course=course"]

    result = run_checker(*arguments)

    issue_kind = "INVALID_SOURCE_ROOT" if option == "--source-root" else "INVALID_NOTES_ROOT"
    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout == (
        f"STRUCTURAL: {issue_kind}: {invalid}: "
        f"{option} must be an existing directory without symlink components\n"
    )
    assert "Traceback" not in result.stdout


def test_source_coverage_cli_rejects_completely_empty_scope(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    source_root.mkdir()
    notes_root.mkdir()

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--strict",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout == (
        "STRUCTURAL: EMPTY_COVERAGE_SCOPE: .: source and notes roots are both "
        "empty; there is no coverage scope to validate\n"
    )


def source_entry(tmp_path: Path) -> SourceEntry:
    path = tmp_path / "sources" / "course" / "lecture.pdf"
    return SourceEntry(
        course_name="course",
        path=path,
        course_relative="lecture.pdf",
        root_relative="course/lecture.pdf",
        name="lecture.pdf",
        stem="lecture",
    )


@pytest.mark.parametrize(
    "text",
    [
        "对应源资料：`course/lecture.pdf`。\n\n另一来源 page 99。\n",
        "| source | locator |\n| --- | --- |\n| `course/lecture.pdf` | none |\n| other | page 99 |\n",
        "- source: `course/lecture.pdf`\n- unrelated: page 99\n",
        "```text\n`course/lecture.pdf` page 99\n```\n",
    ],
    ids=("separate-paragraph", "separate-table-row", "separate-list-item", "code"),
)
def test_page_source_evidence_api_rejects_cross_block_locator_borrowing(
    tmp_path: Path,
    text: str,
) -> None:
    assert page_source_evidence(text, source_entry(tmp_path)) is False


@pytest.mark.parametrize(
    "text",
    [
        "对应源资料：`course/lecture.pdf`，page 3。\n",
        "对应源资料：`course/lecture.pdf`，\npage 3。\n",
        "| source | locator |\n| --- | --- |\n| `course/lecture.pdf` | page 3 |\n",
    ],
    ids=("same-line", "same-paragraph", "same-table-row"),
)
def test_page_source_evidence_api_accepts_associated_locator(
    tmp_path: Path,
    text: str,
) -> None:
    assert page_source_evidence(text, source_entry(tmp_path)) is True


def test_source_coverage_cli_rejects_cross_paragraph_locator_borrowing(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "course" / "lecture.pdf", "fake pdf")
    for name in ("source_manifest.md", "99_内容覆盖审查.md"):
        write(
            notes_root / "course" / name,
            "| source | note |\n| --- | --- |\n"
            "| `course/lecture.pdf` | [[course/01_intro]] |\n",
        )
    write(
        notes_root / "course" / "01_intro.md",
        "---\nsource_files:\n  - course/lecture.pdf\n---\n\n"
        "# Intro\n\n"
        "对应源资料：`course/lecture.pdf`。\n\n"
        "另一本未声明参考书的定位是 page 99。\n\n"
        "Worked example with full steps and a concrete conclusion.\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
        "--strict",
    )

    assert result.returncode == 1
    assert "MANUAL_REVIEW_REQUIRED: MISSING_BODY_SOURCE_EVIDENCE" in result.stdout


def test_check_source_coverage_passes_with_mapping_and_examples(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "课程A" / "ch1.pdf", "fake pdf")
    write(
        notes_root / "课程A笔记" / "source_manifest.md",
        "| source | note |\n|---|---|\n| `ch1.pdf` | `01_导论.md` |\n",
    )
    write(
        notes_root / "课程A笔记" / "99_内容覆盖审查.md",
        "| source | status |\n|---|---|\n| `ch1.pdf` | 已写入 |\n",
    )
    write(
        notes_root / "课程A笔记" / "01_导论.md",
        "\n".join(
            [
                "# 导论",
                "",
                "| 知识点 | 例题/辅助题 | 来源 |",
                "|---|---|---|",
                "| 定义 | 源资料例题：例 1。 | 源资料：（/课程A/ch1）ch1.pdf p.1 |",
                "",
                "## PPT/PDF 页级补充索引",
                "",
                "- 来源：`课程A/ch1.pdf`，页/slide：1；主题：导论；自拟教学例：源课件未提供可独立还原的对应例题；补充题（/课程A/ch1 p.1）：解释导论。",
            ]
        ),
    )
    write(
        notes_root / "非目标目录" / "bad.md",
        "## PPT/PDF 页级补充索引\n\n- 来源：`x`，页/slide：1；主题：x；补充题：需复核。\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "课程A=课程A笔记",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "course_source_files 1" in result.stdout
    assert "coverage_evidence_issues 0" in result.stdout


def test_unmarked_example_content_does_not_replace_provenance(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "course" / "lecture.pdf", "fake pdf")
    write(
        notes_root / "course" / "source_manifest.md",
        "| source | note |\n|---|---|\n| `course/lecture.pdf` | [[course/01_intro]] |\n",
    )
    write(
        notes_root / "course" / "01_intro.md",
        "---\nsource_files:\n  - course/lecture.pdf\n---\n\n"
        "# Intro\n\n对应源资料：`course/lecture.pdf`，page 1。\n\n"
        "例题：给定一个图，判断它是否连通。\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
    )

    assert result.returncode == 1
    assert "NO_EXAMPLE_EVIDENCE" in result.stdout


def test_check_source_coverage_reports_missing_mapping_and_bad_example(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "课程A" / "ch1.pdf", "fake pdf")
    write(notes_root / "课程A笔记" / "source_manifest.md", "| source |\n|---|\n| `other.pdf` |\n")
    write(
        notes_root / "课程A笔记" / "01_导论.md",
        "\n".join(
            [
                "# 导论",
                "",
                "## PPT/PDF 页级补充索引",
                "",
                "- 来源：`课程A/ch1.pdf`，页/slide：1；主题：导论；补充题：解释导论。",
            ]
        ),
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "课程A=课程A笔记",
    )

    assert result.returncode == 1
    assert "MISSING_SOURCE_MAPPING" in result.stdout
    assert "BAD_SUPPLEMENT_EXAMPLE" in result.stdout


def test_check_source_coverage_reports_manual_review_residue(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "课程A" / "ch1.pdf", "fake pdf")
    write(notes_root / "课程A笔记" / "source_manifest.md", "`ch1.pdf`\n")
    write(
        notes_root / "课程A笔记" / "01_导论.md",
        "\n".join(
            [
                "# 导论",
                "",
                "| 知识点 | 例题/辅助题 | 来源 |",
                "|---|---|---|",
                "| 定义 | 源资料例题：例 1。 | 源资料：（/课程A/ch1 p.1） |",
                "",
                "## PPT/PDF 页级补充索引",
                "",
                "- 来源：`课程A/ch1.pdf`，页/slide：1；主题：导论；源资料例题：例 1（/课程A/ch1 p.1）；需复核。",
            ]
        ),
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "课程A=课程A笔记",
    )

    assert result.returncode == 1
    assert "RESIDUAL_MANUAL_REVIEW_MARKER" in result.stdout


def test_check_source_coverage_does_not_infer_owner_from_chapter_ordinals(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "数学模型" / "第八章 离散模型8.1.pdf", "fake pdf")
    write(
        notes_root / "数学模型" / "source_manifest.md",
        "| 源文件 | 对应笔记 |\n"
        "|---|---|\n"
        "| `数学模型/第八章 离散模型8.1.pdf` | [[数学模型/08_第五章_微分方程模型_2]] |\n",
    )
    write(
        notes_root / "数学模型" / "99_内容覆盖审查.md",
        "| 源文件 | 对应笔记 |\n"
        "|---|---|\n"
        "| `数学模型/第八章 离散模型8.1.pdf` | [[数学模型/08_第五章_微分方程模型_2]] |\n",
    )
    write(
        notes_root / "数学模型" / "08_第五章_微分方程模型_2.md",
        "\n".join(
            [
                "# 数学模型：第五章 微分方程模型（2）",
                "",
                "| 知识点 | 例题/辅助题 | 来源 |",
                "|---|---|---|",
                "| 层次分析法 | 源资料例题：例 1。 | 源资料：（/数学模型/ch8）第八章 离散模型8.1.pdf p.7 |",
            ]
        ),
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "数学模型=数学模型",
        "--require-course-prefixed-source-refs",
    )

    assert result.returncode == 1
    assert "STRUCTURAL: MANIFEST_TARGET_OWNER_MISMATCH" in result.stdout
    assert "CHAPTER_MISMATCH_SOURCE_LINK" not in result.stdout
    assert "CHAPTER_MISMATCH_NOTE_SOURCE" not in result.stdout


def test_check_source_coverage_requires_course_prefixed_source_refs(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "编译原理" / "lecture 1.pptx", "fake pptx")
    write(
        notes_root / "编译原理" / "source_manifest.md",
        "| source | note |\n|---|---|\n| `lecture 1.pptx` | [[编译原理/01_词法分析]] |\n",
    )
    write(
        notes_root / "编译原理" / "99_内容覆盖审查.md",
        "| source | note |\n|---|---|\n| `lecture 1.pptx` | [[编译原理/01_词法分析]] |\n",
    )
    write(
        notes_root / "编译原理" / "01_词法分析.md",
        "| 知识点 | 例题/辅助题 | 来源 |\n"
        "|---|---|---|\n"
        "| token | 源资料例题：识别 token。 | 源资料：（/编译原理/lecture 1 p.1） |\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "编译原理=编译原理",
        "--require-course-prefixed-source-refs",
    )

    assert result.returncode == 1
    assert "NONCANONICAL_SOURCE_REF" in result.stdout


def test_check_source_coverage_does_not_treat_lecture_subparts_as_chapter_conflicts(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "编译原理" / "lecture 4b.pptx", "fake pptx")
    write(
        notes_root / "编译原理" / "source_manifest.md",
        "| source | note |\n|---|---|\n| `编译原理/lecture 4b.pptx` | [[编译原理/08_自顶向下语法分析_LL1与FIRST_FOLLOW]] |\n",
    )
    write(
        notes_root / "编译原理" / "99_内容覆盖审查.md",
        "| source | note |\n|---|---|\n| `编译原理/lecture 4b.pptx` | [[编译原理/08_自顶向下语法分析_LL1与FIRST_FOLLOW]] |\n",
    )
    write(
        notes_root / "编译原理" / "08_自顶向下语法分析_LL1与FIRST_FOLLOW.md",
        "---\nsource_files:\n  - \"编译原理/lecture 4b.pptx\"\n---\n"
        + "\n".join(
            [
                "# 第4讲B：自顶向下语法分析、LL(1) 与 FIRST/FOLLOW",
                "",
                "| 知识点 | 例题/辅助题 | 来源 |",
                "|---|---|---|",
                "| FIRST | 源资料例题：计算 FIRST。 | 源资料：`编译原理/lecture 4b.pptx` p.1 |",
            ]
        ),
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "编译原理=编译原理",
        "--require-course-prefixed-source-refs",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_strict_source_coverage_keeps_manifest_audit_and_note_ownership_separate(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "课程A" / "ch1.pdf", "fake pdf")
    write(source_root / "课程A" / "ch2.pdf", "fake pdf")
    write(
        notes_root / "课程A" / "99_内容覆盖审查.md",
        "| source | note |\n|---|---|\n| `课程A/ch1.pdf` | [[课程A/01_导论]] |\n",
    )
    write(
        notes_root / "课程A" / "01_导论.md",
        "---\nsource_files:\n  - \"课程A/ch1.pdf\"\n---\n# 导论\n\n源资料例题：例 1（/课程A/ch1 p.1）。\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "课程A=课程A",
        "--require-four-way-source-coverage",
    )

    assert result.returncode == 1
    assert "MISSING_SOURCE_MANIFEST" in result.stdout
    assert "MISSING_COVERAGE_AUDIT_MAPPING" in result.stdout
    assert "MISSING_NOTE_SOURCE_OWNERSHIP" in result.stdout


def test_strict_mode_discovers_cs231n_and_reconciles_unmapped_source_dirs(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "cs231n" / "lecture_1_part_1.pdf", "fake pdf")
    write(source_root / "orphan" / "lecture.pdf", "fake pdf")
    write(
        notes_root / "cs231n" / "source_manifest.md",
        "| source | note |\n|---|---|\n| `cs231n/lecture_1_part_1.pdf` | [[cs231n/01_intro]] |\n",
    )
    write(
        notes_root / "cs231n" / "99_内容覆盖审查.md",
        "| source | note |\n|---|---|\n| `cs231n/lecture_1_part_1.pdf` | [[cs231n/01_intro]] |\n",
    )
    write(
        notes_root / "cs231n" / "01_intro.md",
        "---\nsource_files:\n  - \"cs231n/lecture_1_part_1.pdf\"\n---\n# Intro\n\n源资料例题：例 1（/cs231n/lecture_1_part_1 p.1）。\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--strict",
    )

    assert result.returncode == 1
    assert "course_source_files 1" in result.stdout
    assert "UNMAPPED_SOURCE_DIR" in result.stdout
    assert "MISSING_SOURCE_MANIFEST" not in result.stdout
    assert "MISSING_NOTE_SOURCE_OWNERSHIP" not in result.stdout


def test_strict_mode_accepts_explicit_full_mapping_for_alias_directories(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    source_name = "All-in-One : Unified Image Restoration"
    notes_name = "all-in-one"
    source_file = f"{source_name}/lecture.pdf"
    write(source_root / source_name / "lecture.pdf", "fake pdf")
    write(
        notes_root / notes_name / "source_manifest.md",
        f"| source | note |\n|---|---|\n| `{source_file}` | [[{notes_name}/01_intro]] |\n",
    )
    write(
        notes_root / notes_name / "99_内容覆盖审查.md",
        f"| source | note |\n|---|---|\n| `{source_file}` | [[{notes_name}/01_intro]] |\n",
    )
    write(
        notes_root / notes_name / "01_intro.md",
        f"---\nsource_files:\n  - \"{source_file}\"\n---\n"
        f"# Intro\n\n源资料例题：例 1（/{source_name}/lecture p.1）。\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--strict",
        "--mapping",
        f"{source_name}={notes_name}",
    )

    assert result.returncode == 1
    assert "source_dir_reconciliation_issues 0" in result.stdout
    assert "structural_issues 0" in result.stdout
    assert "MANUAL_REVIEW_REQUIRED: MISSING_BODY_SOURCE_EVIDENCE" in result.stdout


def test_strict_mode_excludes_standalone_note_systems_from_reconciliation(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    source_root.mkdir()
    for name in ("概念索引", "模板", "游戏数值策划", "科研方法论", "算法岗学习笔记", "学习路径"):
        write(
            notes_root / name / "99_内容覆盖审查.md",
            "---\n"
            "note_type: coverage_audit\n"
            "source_files: []\n"
            "---\n"
            "本目录在本地项目规范中属于笔记侧独立体系，当前根目录没有可确定的一一对应 PPT/PDF 课件目录。\n",
        )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--strict",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "source_dir_reconciliation_issues 0" in result.stdout


def test_unattributed_example_content_does_not_apply_to_the_whole_notes_directory(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    (source_root / "course").mkdir(parents=True)
    write(notes_root / "course" / "01_example.md", "# Example\n\nWorked example with full steps.\n")
    write(notes_root / "course" / "02_summary.md", "# Summary\n\nConcept summary without exercises.\n")

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
    )

    assert "NO_EXAMPLE_EVIDENCE" in result.stdout


def test_default_scan_ignores_scripts_and_caches_but_can_include_them(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "课程A" / "ch1.pdf", "fake pdf")
    for name in ("scripts", ".pytest_cache"):
        write(notes_root / "课程A" / name / "bad.md", "需复核\n")
    write(notes_root / "课程A" / "source_manifest.md", "`ch1.pdf`\n")
    write(notes_root / "课程A" / "99_内容覆盖审查.md", "`ch1.pdf`\n")
    write(
        notes_root / "课程A" / "01_intro.md",
        "# Intro\n\n源资料例题：例 1（/课程A/ch1 p.1）。\n",
    )

    base_args = (
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "课程A=课程A",
    )
    result = run_checker(*base_args)
    assert result.returncode == 0, result.stdout + result.stderr

    included = run_checker(*base_args, "--include-ignored")
    assert included.returncode == 1
    assert "RESIDUAL_MANUAL_REVIEW_MARKER" in included.stdout


def test_paper_source_ownership_checks_five_notes_and_undeclared_body_sources(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    for index in range(1, 6):
        write(source_root / "papers" / f"paper_{index}.pdf", "fake pdf")
        write(
            notes_root / "papers" / f"paper_{index}.md",
            "---\n"
            "note_type: paper_note\n"
            "source_files:\n"
            f"  - \"papers/paper_{index}.pdf\"\n"
            "---\n"
            f"# Paper {index}\n",
        )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--check-paper-source-ownership",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "paper_source_ownership_issues 0" in result.stdout

    write(
        notes_root / "papers" / "paper_1.md",
        (notes_root / "papers" / "paper_1.md").read_text(encoding="utf-8")
        + "参见 `papers/paper_2.pdf`。\n",
    )
    broken = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--check-paper-source-ownership",
    )
    assert broken.returncode == 1
    assert "PAPER_SOURCE_NOT_DECLARED" in broken.stdout


def test_adjacent_source_parts_require_explicit_ownership_evidence(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "course" / "lecture_1_part_1.pdf", "fake pdf")
    write(source_root / "course" / "lecture_1_part_2.pdf", "fake pdf")
    for name in ("source_manifest.md", "99_内容覆盖审查.md"):
        write(
            notes_root / "course" / name,
            "| source | note |\n|---|---|\n"
            "| `course/lecture_1_part_1.pdf` | [[course/01_intro]] |\n"
            "| `course/lecture_1_part_2.pdf` | [[course/01_intro]] |\n",
        )
    write(
        notes_root / "course" / "01_intro.md",
        "---\nsource_files:\n  - \"course/lecture_1_part_1.pdf\"\n---\n# Intro\n\n源资料例题：例 1（/course/lecture_1_part_1 p.1）。\n",
    )
    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
        "--require-four-way-source-coverage",
        "--require-adjacent-source-evidence",
    )
    assert result.returncode == 1
    assert "MISSING_ADJACENT_SOURCE_EVIDENCE" in result.stdout


def test_source_coverage_rejects_mapping_paths_outside_roots(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    source_root.mkdir()
    notes_root.mkdir()

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "../outside=../other",
    )

    assert result.returncode == 1
    assert "MAPPING_SOURCE_OUTSIDE_ROOT" in result.stdout
    assert "MAPPING_NOTES_OUTSIDE_ROOT" in result.stdout


def test_paper_source_ownership_rejects_paths_outside_source_root(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    outside = tmp_path / "outside.pdf"
    write(source_root / "papers" / "paper.pdf", "fake pdf")
    write(outside, "outside pdf")
    write(
        notes_root / "papers" / "paper.md",
        "---\n"
        "note_type: paper_note\n"
        "source_files:\n"
        "  - ../outside.pdf\n"
        f"  - {outside}\n"
        "  - 'C:\\External\\outside.pdf'\n"
        "  - 'C:External\\outside.pdf'\n"
        "  - '\\\\server\\share\\outside.pdf'\n"
        "  - '\\\\?\\C:\\External\\outside.pdf'\n"
        "  - '\\\\.\\PhysicalDrive0.pdf'\n"
        "  - 'C:\\External/mixed\\outside.pdf'\n"
        "---\n"
        "# Paper\n\n"
        "See `../outside.pdf`, `C:\\External\\outside.pdf`, "
        "`C:External\\outside.pdf`, `\\\\server\\share\\outside.pdf`, "
        "`\\\\?\\C:\\External\\outside.pdf`, `\\\\.\\PhysicalDrive0.pdf`, "
        "and `C:\\External/mixed\\outside.pdf`.\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--check-paper-source-ownership",
    )

    assert result.returncode == 1
    assert result.stdout.count("PAPER_SOURCE_OUTSIDE_ROOT") == 15


def test_paper_source_ownership_requires_exact_regular_nonsymlink_files(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    (source_root / "papers" / "directory.pdf").mkdir(parents=True)
    write(source_root / "papers" / "real.pdf", "fake pdf")
    (source_root / "papers" / "alias.pdf").symlink_to(
        source_root / "papers" / "real.pdf"
    )
    (source_root / "papers" / "broken.pdf").symlink_to(
        source_root / "papers" / "missing.pdf"
    )
    (source_root / "papers" / "real-dir").mkdir()
    write(source_root / "papers" / "real-dir" / "nested.pdf", "fake pdf")
    (source_root / "papers" / "linked-dir").symlink_to(
        source_root / "papers" / "real-dir",
        target_is_directory=True,
    )
    for name, source in (
        ("directory", "papers/directory.pdf"),
        ("alias", "papers/alias.pdf"),
        ("broken", "papers/broken.pdf"),
        ("ancestor", "papers/linked-dir/nested.pdf"),
    ):
        write(
            notes_root / "papers" / f"{name}.md",
            "---\n"
            "note_type: paper_note\n"
            "source_files:\n"
            f"  - \"{source}\"\n"
            "---\n"
            f"# {name}\n\n来源：`{source}`。\n",
        )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--check-paper-source-ownership",
    )

    assert result.returncode == 1
    assert result.stdout.count("PAPER_SOURCE_NOT_REGULAR") == 8


def test_exact_regular_source_target_rejects_case_traversal_and_symlinks(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    write(source_root / "Papers" / "Owner.PDF", "fake pdf")
    (source_root / "Papers" / "directory.pdf").mkdir()
    (source_root / "Papers" / "leaf.pdf").symlink_to(
        source_root / "Papers" / "Owner.PDF"
    )
    (source_root / "Papers" / "broken.pdf").symlink_to(
        source_root / "Papers" / "missing.pdf"
    )
    (source_root / "linked").symlink_to(
        source_root / "Papers",
        target_is_directory=True,
    )

    assert exact_regular_source_target(source_root, "Papers/Owner.PDF") == (
        source_root / "Papers" / "Owner.PDF"
    )
    for invalid in (
        "papers/Owner.PDF",
        "Papers/owner.pdf",
        "Papers/directory.pdf",
        "Papers/leaf.pdf",
        "Papers/broken.pdf",
        "linked/Owner.PDF",
        "../outside.pdf",
        str(source_root / "Papers" / "Owner.PDF"),
        r"C:\External\Owner.PDF",
        r"C:External\Owner.PDF",
        r"\\server\share\Owner.PDF",
        r"\\?\C:\External\Owner.PDF",
        r"\\.\PhysicalDrive0.PDF",
        r"C:\External/mixed\Owner.PDF",
    ):
        assert exact_regular_source_target(source_root, invalid) is None


def test_source_scan_excludes_nonregular_and_all_symlink_shapes(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    course = source_root / "course"
    write(course / "real.pdf", "fake pdf")
    (course / "directory.pdf").mkdir()
    (course / "alias.pdf").symlink_to("real.pdf")
    (course / "broken.pdf").symlink_to("missing.pdf")
    write(course / "real-dir" / "nested.pptx", "fake pptx")
    (course / "linked-dir").symlink_to("real-dir", target_is_directory=True)
    outside = tmp_path / "outside.pdf"
    write(outside, "outside")
    (course / "external.pdf").symlink_to(outside)

    scanned = [path.relative_to(source_root).as_posix() for path in source_files(source_root)]
    issues = {(issue.kind, issue.path.name) for issue in source_boundary_issues(source_root)}

    assert scanned == ["course/real-dir/nested.pptx", "course/real.pdf"]
    assert issues == {
        ("source_symlink", "alias.pdf"),
        ("source_symlink", "broken.pdf"),
        ("source_symlink", "linked-dir"),
        ("source_symlink_outside_root", "external.pdf"),
    }


def test_source_scan_cli_reports_symlinks_as_structural_issues(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    course = source_root / "course"
    write(course / "real.pdf", "fake pdf")
    (course / "directory.pdf").mkdir()
    (course / "alias.pdf").symlink_to("real.pdf")
    (course / "broken.pdf").symlink_to("missing.pdf")
    write(course / "real-dir" / "nested.pptx", "fake pptx")
    (course / "linked-dir").symlink_to("real-dir", target_is_directory=True)
    outside = tmp_path / "outside.pdf"
    write(outside, "outside")
    (course / "external.pdf").symlink_to(outside)
    (notes_root / "course").mkdir(parents=True)

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
    )

    assert result.returncode == 1
    assert "course_source_files 2" in result.stdout
    assert "STRUCTURAL: SOURCE_SYMLINK:" in result.stdout
    assert "STRUCTURAL: SOURCE_SYMLINK_OUTSIDE_ROOT:" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("actual", "lookalike"),
    [
        ("A.pdf", "Ａ.pdf"),
        ("1.pdf", "１.pdf"),
    ],
    ids=("fullwidth-letter", "fullwidth-digit"),
)
def test_exact_regular_source_target_does_not_nfkc_fold_compatibility_names(
    tmp_path: Path,
    actual: str,
    lookalike: str,
) -> None:
    source_root = tmp_path / "sources"
    write(source_root / actual, "fake pdf")

    assert exact_regular_source_target(source_root, actual) == source_root / actual
    assert exact_regular_source_target(source_root, lookalike) is None


def test_paper_source_ownership_rejects_nfkc_compatibility_lookalike(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "papers" / "A.pdf", "fake pdf")
    write(
        notes_root / "papers" / "paper.md",
        "---\n"
        "note_type: paper_note\n"
        "source_files:\n"
        '  - "papers/Ａ.pdf"\n'
        "---\n"
        "# Paper\n\n来源：`papers/Ａ.pdf`。\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--check-paper-source-ownership",
    )

    assert result.returncode == 1
    assert result.stdout.count("PAPER_SOURCE_NOT_FOUND") == 2


@pytest.mark.parametrize(
    "invalid",
    (
        "/absolute/Owner.PDF",
        r"C:\External\Owner.PDF",
        r"C:External\Owner.PDF",
        r"\\server\share\Owner.PDF",
        r"\\?\C:\External\Owner.PDF",
        r"\\.\PhysicalDrive0.PDF",
        r"C:\External/mixed\Owner.PDF",
    ),
)
def test_source_path_apis_reject_cross_platform_anchors(
    tmp_path: Path,
    invalid: str,
) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()

    assert resolve_beneath(source_root, invalid) is None
    assert exact_regular_source_target(source_root, invalid) is None


def test_source_coverage_reports_source_symlink_outside_root(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    outside = tmp_path / "outside.pdf"
    write(outside, "outside")
    (source_root / "course").mkdir(parents=True)
    (source_root / "course" / "external.pdf").symlink_to(outside)
    write(notes_root / "course" / "source_manifest.md", "`course/external.pdf`\n")
    write(notes_root / "course" / "99_内容覆盖审查.md", "`course/external.pdf`\n")

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
    )

    assert result.returncode == 1
    assert "SOURCE_SYMLINK_OUTSIDE_ROOT" in result.stdout


def test_real_notes_adapter_missing_page_evidence_is_manual_review_not_full_coverage(tmp_path: Path) -> None:
    """Mirror the current course-note shape without changing the real notes repo."""

    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    source = "编译原理/lecture 4b.pptx"
    write(source_root / source, "fake pptx")
    for name in ("source_manifest.md", "99_内容覆盖审查.md"):
        write(
            notes_root / "编译原理" / name,
            f"| 源文件 | 对应笔记 |\n|---|---|\n| `{source}` | [[编译原理/08_自顶向下语法分析_LL1与FIRST_FOLLOW]] |\n",
        )
    write(
        notes_root / "编译原理" / "08_自顶向下语法分析_LL1与FIRST_FOLLOW.md",
        "---\n"
        "course: 编译原理\n"
        "source_files:\n"
        f"  - {source}\n"
        "---\n"
        "# 第4讲B：自顶向下语法分析、LL(1) 与 FIRST/FOLLOW\n\n"
        f"对应源资料：`{source}`。本页保留文本抽取内容，但没有页或 slide 定位。\n"
        "本页的例题来自课程内容。\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "编译原理=编译原理",
        "--strict",
        "--require-course-prefixed-source-refs",
    )

    assert result.returncode == 1
    assert "MANUAL_REVIEW_REQUIRED: MISSING_BODY_SOURCE_EVIDENCE" in result.stdout
    assert "structural_issues 0" in result.stdout
    assert "manual_review_required_issues" in result.stdout
    assert "fully covered" not in result.stdout.lower()


def test_real_notes_adapter_chinese_source_topic_is_not_a_false_owner_failure(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    source = "可计算性理论/第一讲 导引 +（有穷状态自动机）.ppt"
    write(source_root / source, "fake ppt")
    for name in ("source_manifest.md", "99_内容覆盖审查.md"):
        write(
            notes_root / "可计算性理论" / name,
            f"| 源文件 | 对应笔记 |\n|---|---|\n| `{source}` | [[可计算性理论/01_导引与有穷状态自动机]] |\n",
        )
    write(
        notes_root / "可计算性理论" / "01_导引与有穷状态自动机.md",
        "---\n"
        "source_files:\n"
        f"  - {source}\n"
        "---\n"
        "# 第1讲：导引与有穷状态自动机\n\n"
        f"对应源资料：`{source}`，p.1。\n"
        "源资料例题：例 1（/可计算性理论/第一讲 p.1）。\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "可计算性理论=可计算性理论",
        "--strict",
        "--require-course-prefixed-source-refs",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SOURCE_TOPIC_OWNER_MISMATCH" not in result.stdout
    assert "source_topic_owner_unproven" not in result.stdout


def test_real_notes_adapter_wrong_frontmatter_owner_remains_structural_fail(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    source = "课程A/03_概率模型.pdf"
    target = "课程A/03_线性代数"
    write(source_root / source, "fake pdf")
    for name in ("source_manifest.md", "99_内容覆盖审查.md"):
        write(
            notes_root / "课程A" / name,
            f"| 源文件 | 对应笔记 |\n|---|---|\n| `{source}` | [[{target}]] |\n",
        )
    write(
        notes_root / "课程A" / "03_线性代数.md",
        "---\n"
        "source_files:\n"
        "  - 课程A/03_错误来源.pdf\n"
        "---\n"
        "# 03_线性代数\n\n"
        f"对应源资料：`{source}`，p.1。\n"
        "源资料例题：例 1（/课程A/03 p.1）。\n",
    )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "课程A=课程A",
        "--strict",
        "--require-course-prefixed-source-refs",
    )

    assert result.returncode == 1
    assert "STRUCTURAL: MANIFEST_TARGET_OWNER_MISMATCH" in result.stdout
    assert "STRUCTURAL: MISSING_FRONTMATTER_SOURCE_EVIDENCE" in result.stdout
    assert "structural_issues" in result.stdout


def write_strict_complete_course(source_root: Path, notes_root: Path) -> None:
    source = "course/lecture.pdf"
    write(source_root / source, "fake pdf")
    for name in ("source_manifest.md", "99_内容覆盖审查.md"):
        write(
            notes_root / "course" / name,
            f"| source | note |\n|---|---|\n| `{source}` | [[course/01_intro]] |\n",
        )
    write(
        notes_root / "course" / "01_intro.md",
        "---\n"
        "source_files:\n"
        f"  - {source}\n"
        "---\n"
        "# Intro\n\n"
        f"对应源资料：`{source}` p.1。源资料例题：例 1（/course/lecture p.1）。\n",
    )


def test_strict_source_coverage_rejects_unknown_local_source_refs(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write_strict_complete_course(source_root, notes_root)
    for name in ("source_manifest.md", "99_内容覆盖审查.md"):
        artifact = notes_root / "course" / name
        artifact.write_text(
            artifact.read_text(encoding="utf-8")
            + "| `course/ghost.pdf` | [[course/01_intro]] |\n",
            encoding="utf-8",
        )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
        "--strict",
    )

    assert result.returncode == 1
    assert result.stdout.count("STRUCTURAL: UNKNOWN_SOURCE_REF:") == 2
    assert "course/ghost.pdf" in result.stdout


def test_strict_source_coverage_allows_external_url_provenance(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write_strict_complete_course(source_root, notes_root)
    for name in ("source_manifest.md", "99_内容覆盖审查.md"):
        artifact = notes_root / "course" / name
        artifact.write_text(
            artifact.read_text(encoding="utf-8")
            + "External provenance: `https://example.com/paper.pdf`.\n",
            encoding="utf-8",
        )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
        "--strict",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "UNKNOWN_SOURCE_REF" not in result.stdout


def test_visible_source_references_keep_semantic_inline_refs_and_mask_code() -> None:
    text = (
        "Visible source: `course/real.pdf`.\n\n"
        "```text\n`course/fenced.pdf`\n```\n\n"
        "~~~text\n`course/tilde.pdf`\n~~~\n\n"
        "    `course/indented.pdf`\n\n"
        "> ```text\n> `course/quote.pdf`\n> ```\n\n"
        "- ```text\n  `course/list.pdf`\n  ```\n\n"
        "<!-- `course/html-comment.pdf` -->\n"
        "%% `course/obsidian-comment.pdf` %%\n"
    )

    references = visible_source_references(text)

    assert [(line_number, ref) for line_number, ref, _line in references] == [
        (1, "course/real.pdf")
    ]


def test_strict_source_coverage_rejects_invalid_local_reference_matrix(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write_strict_complete_course(source_root, notes_root)
    (source_root / "course" / "directory.pdf").mkdir()
    (source_root / "course" / "alias.pdf").symlink_to("lecture.pdf")
    write(tmp_path / "outside.pdf", "outside")
    visible_rows = (
        "| `course/missing.pdf` | [[course/01_intro]] |\n"
        "| `course/directory.pdf` | [[course/01_intro]] |\n"
        "| `course/alias.pdf` | [[course/01_intro]] |\n"
        "| `../outside.pdf` | [[course/01_intro]] |\n"
        "| `C:\\Outside\\paper.pdf` | [[course/01_intro]] |\n"
        "| `course/LECTURE.pdf` | [[course/01_intro]] |\n"
        "External provenance: `https://example.com/paper.pdf`.\n"
    )
    hidden_rows = (
        "```text\n`course/fenced-only.pdf`\n```\n"
        "<!-- `course/comment-only.pdf` -->\n"
    )
    for name in ("source_manifest.md", "99_内容覆盖审查.md"):
        artifact = notes_root / "course" / name
        artifact.write_text(
            artifact.read_text(encoding="utf-8") + visible_rows + hidden_rows,
            encoding="utf-8",
        )

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
        "--strict",
    )

    assert result.returncode == 1
    assert result.stdout.count("STRUCTURAL: UNKNOWN_SOURCE_REF:") == 10
    assert result.stdout.count("STRUCTURAL: NONCANONICAL_SOURCE_REF:") == 2
    assert "STRUCTURAL: SOURCE_SYMLINK:" in result.stdout
    for invalid in (
        "course/missing.pdf",
        "course/directory.pdf",
        "course/alias.pdf",
        "../outside.pdf",
        r"C:\\Outside\\paper.pdf",
    ):
        assert invalid in result.stdout
    assert "fenced-only.pdf" not in result.stdout
    assert "comment-only.pdf" not in result.stdout
    assert "https://example.com/paper.pdf" not in result.stdout
    assert "Traceback" not in result.stderr


def test_strict_source_coverage_accepts_exact_multi_source_ownership(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    sources = (
        ("course/alpha.pdf", "course/01_alpha"),
        ("course/beta.pptx", "course/02_beta"),
    )
    rows = ["| source | note |", "|---|---|"]
    for source, note in sources:
        write(source_root / source, "fake source")
        rows.append(f"| `{source}` | [[{note}]] |")
        note_path = notes_root / f"{note}.md"
        write(
            note_path,
            "---\n"
            "source_files:\n"
            f"  - {source}\n"
            "---\n"
            f"# {Path(note).name}\n\n"
            f"对应源资料：`{source}` p.1。源资料例题：例 1（/{Path(source).stem} p.1）。\n",
        )
    table = "\n".join(rows) + "\n"
    write(notes_root / "course" / "source_manifest.md", table)
    write(notes_root / "course" / "99_内容覆盖审查.md", table)

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
        "--strict",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "course_source_files 2" in result.stdout
    assert "coverage_evidence_issues 0" in result.stdout


@pytest.mark.parametrize("artifact_name", ["source_manifest.md", "99_内容覆盖审查.md"])
def test_strict_source_coverage_rejects_external_fixed_artifact_symlink(
    tmp_path: Path,
    artifact_name: str,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write_strict_complete_course(source_root, notes_root)
    artifact = notes_root / "course" / artifact_name
    content = artifact.read_text(encoding="utf-8")
    artifact.unlink()
    outside = tmp_path / artifact_name
    write(outside, content)
    artifact.symlink_to(outside)

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
        "--strict",
    )

    assert result.returncode == 1
    assert "NOTES_ARTIFACT_SYMLINK_OUTSIDE_ROOT" in result.stdout
    assert "STRUCTURAL" in result.stdout


def test_strict_source_coverage_accepts_regular_fixed_artifacts(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write_strict_complete_course(source_root, notes_root)

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
        "--strict",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_fixed_artifact_symlink_in_ignored_directory_requires_include_ignored(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    (source_root / "course").mkdir(parents=True)
    (notes_root / "course").mkdir(parents=True)
    outside = tmp_path / "outside_manifest.md"
    write(outside, "# External manifest\n")
    ignored_artifact = notes_root / ".obsidian" / "source_manifest.md"
    ignored_artifact.parent.mkdir()
    ignored_artifact.symlink_to(outside)
    args = (
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
    )

    default_result = run_checker(*args)
    included_result = run_checker(*args, "--include-ignored")

    assert default_result.returncode == 0, default_result.stdout + default_result.stderr
    assert "NOTES_ARTIFACT_SYMLINK" not in default_result.stdout
    assert included_result.returncode == 1
    assert "NOTES_ARTIFACT_SYMLINK_OUTSIDE_ROOT" in included_result.stdout


def test_markdown_scan_ignores_md_directories(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    (source_root / "course").mkdir(parents=True)
    (notes_root / "course" / "directory.md").mkdir(parents=True)

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_manifest_target_md_directory_is_structured_missing_note_not_traceback(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    notes_root = tmp_path / "notes"
    write(source_root / "course" / "lecture.pdf", "fixture")
    write(
        notes_root / "course" / "source_manifest.md",
        "# Manifest\n\n`course/lecture.pdf` [[owner.md]]\n",
    )
    write(
        notes_root / "course" / "99_内容覆盖审查.md",
        "# Audit\n\n`course/lecture.pdf`\n",
    )
    (notes_root / "course" / "owner.md").mkdir()

    result = run_checker(
        "--source-root",
        str(source_root),
        "--notes-root",
        str(notes_root),
        "--mapping",
        "course=course",
    )

    assert result.returncode == 1
    assert "MANIFEST_TARGET_MISSING_NOTE" in result.stdout
    assert "Traceback" not in result.stderr


def test_resolve_note_target_requires_exact_regular_nonsymlink_path(
    tmp_path: Path,
) -> None:
    notes_root = tmp_path / "notes"
    notes_dir = notes_root / "course"
    write(notes_dir / "Owner.md", "# Owner\n")
    (notes_dir / "directory.md").mkdir()
    (notes_dir / "leaf.md").symlink_to(notes_dir / "Owner.md")
    (notes_dir / "broken.md").symlink_to(notes_dir / "missing.md")
    outside_dir = tmp_path / "outside"
    write(outside_dir / "linked.md", "# Linked\n")
    (notes_dir / "ancestor").symlink_to(outside_dir, target_is_directory=True)

    assert resolve_note_target(notes_dir, "Owner.md") == notes_dir / "Owner.md"
    assert resolve_note_target(notes_dir, "owner.md") is None
    assert resolve_note_target(notes_dir, "directory.md") is None
    assert resolve_note_target(notes_dir, "leaf.md") is None
    assert resolve_note_target(notes_dir, "broken.md") is None
    assert resolve_note_target(notes_dir, "ancestor/linked.md") is None
    assert resolve_note_target(notes_dir, "../outside/linked.md") is None

    # Literal POSIX directories can otherwise make foreign-platform anchors
    # look like valid in-root paths after slash normalization.
    write(notes_root / "C:" / "course" / "Owner.md", "# Drive lookalike\n")
    for anchored in (
        "/course/Owner.md",
        r"C:\course\Owner.md",
        r"C:course\Owner.md",
        r"\\server\share\Owner.md",
        r"\\?\C:\course\Owner.md",
        r"\\.\PhysicalDrive0",
        r"C:\course/mixed\Owner.md",
    ):
        assert resolve_note_target(notes_dir, anchored) is None

    real_root = tmp_path / "real-notes"
    write(real_root / "course" / "Owner.md", "# Owner\n")
    symlinked_root = tmp_path / "symlinked-notes"
    symlinked_root.symlink_to(real_root, target_is_directory=True)
    assert resolve_note_target(symlinked_root / "course", "Owner.md") is None
