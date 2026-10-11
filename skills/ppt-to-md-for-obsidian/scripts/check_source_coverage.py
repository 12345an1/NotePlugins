#!/usr/bin/env python3
"""Validate source-to-note coverage evidence for PPT/PDF course notes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys
import unicodedata
from typing import Optional

try:
    from .check_obsidian_links import COMMENT_SPAN_RE, text_without_code
    from .safe_io import ensure_safe_input_directory
except ImportError:
    from check_obsidian_links import COMMENT_SPAN_RE, text_without_code
    from safe_io import ensure_safe_input_directory


SOURCE_EXTENSIONS = {".pdf", ".ppt", ".pptx"}
SOURCE_EXAMPLE_LABELS = ("源资料例题", "源课件例题")
GENERATED_MARKER = "自拟教学例：源课件未提供可独立还原的对应例题"
GENERATED_MARKERS = (
    GENERATED_MARKER,
    "生成：PPT/PDF 未提供独立可抽取例题",
)
EXPLICIT_SELF_WRITTEN_LABEL_RE = re.compile(
    r"^\s*(?:来源说明[:：]\s*)?(?:自拟|自编|原创)(?:教学)?"
    r"(?:例题|案例|示例|练习|题目|题|例)(?:[：:；;（(]|$)"
)
NATURAL_GENERATED_SOURCE_PATTERNS = (
    re.compile(
        r"(?:自拟|自编|原创).{0,120}(?:源课件|课件|源资料|论文|讲义).{0,120}"
        r"(?:未提供|没有提供|未给出|没有给出|未包含|没有包含).{0,80}"
        r"(?:例题|练习|题目|案例|示例|数值|数据|条件|答案|解法|对应)",
        re.S,
    ),
    re.compile(
        r"(?:源课件|课件|源资料|论文|讲义).{0,120}"
        r"(?:未提供|没有提供|未给出|没有给出|未包含|没有包含).{0,80}"
        r"(?:例题|练习|题目|案例|示例|数值|数据|条件|答案|解法|对应).{0,120}"
        r"(?:自拟|自编|原创)",
        re.S,
    ),
    re.compile(
        r"(?:自拟|自编|原创).{0,80}(?:并非|不是|不属于|非)\s*"
        r"(?:源课件|课件|源资料|论文|讲义).{0,40}(?:原题|例题|练习|题目|案例|示例)",
        re.S,
    ),
)
NEGATED_SELF_WRITTEN_RE = re.compile(
    r"(?:不是|并非|不属于|不算|不能算|称不上|谈不上|非)\s*"
    r"(?:(?:真正(?:意义上)?|所谓|严格意义上|课件中|课程中|源课件中|源资料中|论文中)的?\s*)?"
    r"(?:一(?:个|道|项|份))?\s*"
    r"(?:自拟|自编|原创)(?:教学)?(?:题|例|例题|练习|案例|示例)?"
)
EXAMPLE_CONTENT_MARKERS = ("例题", "例子", "练习", "worked example", "worked examples")
NO_EXAMPLE_DECLARATIONS = (
    "未提供独立",
    "没有独立",
    "无独立",
    "不再据此声称",
    "无可独立",
)
SUPPLEMENT_HEADING = "## PPT/PDF 页级补充索引"
EXAMPLE_HEADING = "## PPT/PDF 例题辅助理解"
SOURCE_REF_RE = re.compile(r"`([^`]+\.(?:pdf|pptx?|PDF|PPTX?))`")
SOURCE_FILE_MARKER_RE = re.compile(
    r"\.(?:pdf|pptx?)(?:\b|(?=[`#?]))",
    re.I,
)
WIKI_LINK_RE = re.compile(r"\[\[([^]|#]+)(?:#[^]|]+)?(?:\|[^\]]+)?\]\]")
CHAPTER_RE = re.compile(r"第\s*([零〇一二三四五六七八九十百\d]+)\s*[章节章]")
LECTURE_RE = re.compile(r"第\s*([零〇一二三四五六七八九十百\d]+)\s*[讲講]|lecture\s*0*(\d+[a-z]?)", re.I)
EVIDENCE_MARKERS = ("来源", "源资料", "源课件", "补充题", "页/slide")
PAGE_EVIDENCE_RE = re.compile(
    r"(?:页\s*/?\s*slide|slide|page|p\.)\s*[:：]?\s*\d+|页\s*[:：]?\s*\d+",
    re.I,
)
EVIDENCE_LIST_ITEM_RE = re.compile(r"^(?:[-+*]|\d{1,9}[.)])(?:\s+|$)")
EVIDENCE_HEADING_RE = re.compile(r"^#{1,6}(?:\s+|$)")
LEGACY_FAILURE_LABELS = ("PPT不可读", "PDF不可读", "待人工确认", "待手工确认")
RESIDUAL_REVIEW_MARKERS = (
    "需复核",
    "人工确认",
    "人工打开",
    "手动打开",
    "打开课件确认",
    "open the slides manually",
)
DEFAULT_IGNORED_DIR_NAMES = frozenset(
    {
        ".git",
        ".obsidian",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "converted_pptx",
        "node_modules",
        "output",
        "scripts",
        "skills",
    }
)
DEFAULT_IGNORED_FILE_NAMES = frozenset({"AGENT.md"})
FIXED_NOTES_ARTIFACT_NAMES = frozenset({"source_manifest.md", "99_内容覆盖审查.md"})
STANDALONE_NOTES_DIR_NAMES = frozenset(
    {"概念索引", "模板", "游戏数值策划", "科研方法论", "算法岗学习笔记", "学习路径"}
)
STANDALONE_NOTE_TYPES = frozenset({"concept_index", "standalone", "template", "methodology"})
CHINESE_NUMBERS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


@dataclass(frozen=True)
class CoverageIssue:
    kind: str
    path: Path
    message: str
    status: str = "STRUCTURAL"


@dataclass(frozen=True)
class SourceEntry:
    course_name: str
    path: Path
    course_relative: str
    root_relative: str
    name: str
    stem: str
    # Compatibility metadata only.  Owner decisions must not use a source
    # stem or numeric ordinal; manifest/frontmatter/body checks own that gate.
    chapter_signature: Optional[str] = None


ROOT_ERROR_REASON = "must be an existing directory without symlink components"


def validate_scan_root(path: Path, option: str) -> tuple[Path | None, CoverageIssue | None]:
    """Validate a CLI scan root without resolving away lexical symlinks."""

    lexical = Path(os.path.abspath(path.expanduser()))
    try:
        safe = ensure_safe_input_directory(lexical)
    except (OSError, ValueError):
        kind = "invalid_source_root" if option == "--source-root" else "invalid_notes_root"
        return None, CoverageIssue(
            kind,
            lexical,
            f"{option} {ROOT_ERROR_REASON}",
        )
    return safe, None


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"\s+", " ", normalized)


def normalize_path_text(value: str) -> str:
    # Path ownership is an exact-spelling contract. Compatibility
    # normalization would make distinct entries such as A.pdf and Ａ.pdf
    # interchangeable even though they are different source names.
    return value.replace("\\", "/").strip()


def has_cross_platform_anchor(value: str) -> bool:
    """Reject native absolutes plus Windows drives, UNC, and device anchors."""

    windows_path = PureWindowsPath(value)
    return Path(value).is_absolute() or bool(windows_path.drive or windows_path.anchor)


def chinese_number_to_int(value: str) -> Optional[int]:
    value = value.strip()
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = CHINESE_NUMBERS.get(left, 1) if left else 1
        ones = CHINESE_NUMBERS.get(right, 0) if right else 0
        return tens * 10 + ones
    return CHINESE_NUMBERS.get(value)


def extract_chapter_signature(text: str) -> Optional[str]:
    """Extract explicit chapter/lecture metadata for compatibility callers.

    Numeric filename ordinals are intentionally ignored.  This metadata is
    not an owner decision; canonical manifest/frontmatter/body checks are.
    """

    match = CHAPTER_RE.search(text)
    if match:
        number = chinese_number_to_int(match.group(1))
        if number is not None:
            return f"chapter:{number}"
    match = LECTURE_RE.search(text)
    if match:
        value = match.group(1) or match.group(2)
        if value:
            number = chinese_number_to_int(value)
            return f"lecture:{number if number is not None else value.lower()}"
    return None


def chapter_signatures_conflict(source_signature: Optional[str], target_signature: Optional[str]) -> bool:
    """Compare explicit metadata for legacy callers, never filename ordinals."""

    if not source_signature or not target_signature:
        return False
    if not source_signature.startswith("chapter:") or not target_signature.startswith("chapter:"):
        return False
    return source_signature != target_signature


def source_topic_compatible(entry: SourceEntry, note_path: Path) -> Optional[bool]:
    """Return only positive body evidence; never infer a mismatch from names.

    ``None`` means the source/note topic is not automatically provable and is
    therefore a manual-review case.  This compatibility API deliberately has
    no negative stem-to-title inference.
    """

    if page_source_evidence(note_body_text(note_path), entry):
        return True
    return None


def parse_mapping(value: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise argparse.ArgumentTypeError(f"invalid mapping item, expected source=notes: {item}")
        source, notes = item.split("=", 1)
        source = source.strip()
        notes = notes.strip()
        if not source or not notes:
            raise argparse.ArgumentTypeError(f"invalid mapping item, expected source=notes: {item}")
        mapping[source] = notes
    return mapping


def resolve_beneath(root: Path, raw: str) -> Path | None:
    """Resolve a user-provided path while refusing root escapes and symlinks."""

    normalized = normalize_path_text(raw)
    if not normalized:
        return None
    if has_cross_platform_anchor(normalized):
        return None
    root = root.expanduser().resolve()
    candidate = (root / normalized).expanduser().resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def exact_regular_source_target(root: Path, raw: str) -> Path | None:
    """Return an exact regular source path without symlink components."""

    normalized = normalize_path_text(raw)
    target = Path(normalized)
    if not normalized or has_cross_platform_anchor(normalized):
        return None
    if any(component in {"", ".", ".."} for component in target.parts):
        return None
    candidate = root / target
    if not is_within_root(root, candidate):
        return None
    if not is_regular_file_without_symlink_components(root, candidate):
        return None
    return candidate


def validate_mapping_paths(
    source_root: Path, notes_root: Path, mapping: dict[str, str]
) -> tuple[dict[str, str], list[CoverageIssue]]:
    """Drop mappings that would make the checker read outside either root."""

    safe_mapping: dict[str, str] = {}
    issues: list[CoverageIssue] = []
    for source_name, notes_name in mapping.items():
        source_path = resolve_beneath(source_root, source_name)
        notes_path = resolve_beneath(notes_root, notes_name)
        if source_path is None:
            issues.append(
                CoverageIssue(
                    "mapping_source_outside_root",
                    source_root,
                    f"--mapping source path escapes source root: {source_name!r}",
                )
            )
        if notes_path is None:
            issues.append(
                CoverageIssue(
                    "mapping_notes_outside_root",
                    notes_root,
                    f"--mapping notes path escapes notes root: {notes_name!r}",
                )
            )
        if source_path is not None and notes_path is not None:
            safe_mapping[source_name] = notes_name
    return safe_mapping, issues


def _path_is_ignored(path: Path, root: Path, include_ignored: bool) -> bool:
    if include_ignored:
        return False
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        relative_parts = path.parts
    return path.name in DEFAULT_IGNORED_FILE_NAMES or bool(set(relative_parts) & DEFAULT_IGNORED_DIR_NAMES)


def is_within_root(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def is_regular_file_without_symlink_components(root: Path, path: Path) -> bool:
    try:
        root_mode = root.lstat().st_mode
    except OSError:
        return False
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        return False
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    current = root
    try:
        for index, component in enumerate(relative.parts):
            # Resolve each component through an exact directory-entry lookup.
            # This keeps checks case-sensitive even on a case-insensitive host.
            entry = next(
                (candidate for candidate in current.iterdir() if candidate.name == component),
                None,
            )
            if entry is None:
                return False
            current = entry
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                return False
            if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
                return False
        return bool(relative.parts) and stat.S_ISREG(mode)
    except OSError:
        return False


def notes_artifact_boundary_issues(
    notes_root: Path,
    include_ignored: bool = False,
) -> list[CoverageIssue]:
    """Reject fixed manifest/audit artifacts reached through symlinks."""

    issues: list[CoverageIssue] = []
    if not notes_root.is_dir():
        return issues
    for path in sorted(notes_root.rglob("*")):
        if _path_is_ignored(path, notes_root, include_ignored):
            continue
        if path.name not in FIXED_NOTES_ARTIFACT_NAMES:
            continue
        try:
            if not stat.S_ISLNK(path.lstat().st_mode):
                continue
        except OSError:
            continue
        try:
            path.resolve().relative_to(notes_root.resolve())
            kind = "notes_artifact_symlink"
            message = "fixed notes artifact must be a regular file, not a symlink"
        except ValueError:
            kind = "notes_artifact_symlink_outside_root"
            message = f"fixed notes artifact symlink resolves outside notes root: {path.resolve()}"
        issues.append(CoverageIssue(kind, path, message))
    return issues


def markdown_files(root: Path, include_ignored: bool = False) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if is_regular_file_without_symlink_components(root, path)
        and is_within_root(root, path)
        and not _path_is_ignored(path, root, include_ignored)
    )


def source_files(source_root: Path, include_ignored: bool = False) -> list[Path]:
    return sorted(
        path
        for path in source_root.rglob("*")
        if is_within_root(source_root, path)
        and is_regular_file_without_symlink_components(source_root, path)
        and path.suffix.lower() in SOURCE_EXTENSIONS
        and not _path_is_ignored(path, source_root, include_ignored)
    )


def source_boundary_issues(source_root: Path, include_ignored: bool = False) -> list[CoverageIssue]:
    """Report every source-tree symlink, including in-root and broken links."""

    if not source_root.is_dir():
        return []
    issues: list[CoverageIssue] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_symlink() or _path_is_ignored(path, source_root, include_ignored):
            continue
        outside_root = not is_within_root(source_root, path)
        issues.append(
            CoverageIssue(
                "source_symlink_outside_root" if outside_root else "source_symlink",
                path,
                "source tree symlinks are not allowed; source inputs must be regular "
                "files reached without symlink components",
            )
        )
    return issues


def build_source_entries(
    source_root: Path, source_name: str, include_ignored: bool = False
) -> list[SourceEntry]:
    source_dir = source_root / source_name
    entries: list[SourceEntry] = []
    for path in source_files(source_dir, include_ignored=include_ignored):
        course_relative = path.relative_to(source_dir).as_posix()
        root_relative = path.relative_to(source_root).as_posix()
        entries.append(
            SourceEntry(
                course_name=source_name,
                path=path,
                course_relative=course_relative,
                root_relative=root_relative,
                name=path.name,
                stem=path.stem,
            )
        )
    return entries


def discover_sibling_mappings(
    source_root: Path,
    notes_root: Path,
    explicit_mapping: dict[str, str],
    include_ignored: bool = False,
) -> dict[str, str]:
    """Discover source directories whose note directory has the same name.

    Explicit mappings always win.  This catches source-only additions such as
    ``cs231n`` without guessing across differently named directories such as
    ``dehaze`` and ``去雾``.
    """

    mapping = dict(explicit_mapping)
    if not source_root.is_dir() or not notes_root.is_dir():
        return mapping
    for source_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        source_name = source_dir.name
        if not include_ignored and source_name in DEFAULT_IGNORED_DIR_NAMES:
            continue
        if source_name in mapping:
            continue
        if not source_files(source_dir, include_ignored=include_ignored):
            continue
        notes_dir = notes_root / source_name
        if notes_dir.is_dir() and markdown_files(notes_dir, include_ignored=include_ignored):
            mapping[source_name] = source_name
    return mapping


def course_text(notes_dir: Path) -> str:
    pieces: list[str] = []
    for name in ("source_manifest.md", "99_内容覆盖审查.md"):
        path = notes_dir / name
        if is_regular_file_without_symlink_components(notes_dir, path):
            pieces.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(pieces)


def frontmatter(path: Path) -> dict[str, object]:
    """Read the small YAML subset used by solvenotes without requiring PyYAML."""

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return {}
    values: dict[str, object] = {}
    current_list: str | None = None
    for line in lines[1:end]:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$", line)
        if match:
            key, raw = match.group(1), (match.group(2) or "").strip()
            current_list = None
            if raw == "" and key == "source_files":
                values[key] = []
                current_list = key
            elif raw == "[]":
                values[key] = []
                current_list = key
            elif raw.startswith("[") and raw.endswith("]"):
                values[key] = _parse_inline_list(raw)
                current_list = key
            else:
                values[key] = _strip_yaml_scalar(raw)
            continue
        if current_list is not None:
            item = re.match(r"^\s*-\s*(.*?)\s*$", line)
            if item:
                values.setdefault(current_list, [])
                if isinstance(values[current_list], list):
                    values[current_list].append(_strip_yaml_scalar(item.group(1)))
    return values


def note_body_text(path: Path) -> str:
    """Return note body without YAML frontmatter source declarations."""

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return "\n".join(lines)
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return "\n".join(lines)
    return "\n".join(lines[end + 1 :])


def _strip_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_inline_list(value: str) -> list[str]:
    inner = value[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    for match in re.finditer(r'''"([^"\\]*(?:\\.[^"\\]*)*)"|'([^']*)'|([^,]+)''', inner):
        raw = match.group(1) or match.group(2) or match.group(3) or ""
        items.append(_strip_yaml_scalar(raw.strip()))
    return items


def frontmatter_note_type(path: Path) -> str:
    value = frontmatter(path).get("note_type", "")
    return str(value).strip().strip('"\'')


def frontmatter_source_files(path: Path) -> list[str]:
    value = frontmatter(path).get("source_files", [])
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def is_standalone_notes_dir(notes_dir: Path) -> bool:
    """Exclude known non-course note systems from source-dir reconciliation."""

    if notes_dir.name in STANDALONE_NOTES_DIR_NAMES:
        return True
    for artifact_name in ("source_manifest.md", "99_内容覆盖审查.md"):
        artifact = notes_dir / artifact_name
        if not is_regular_file_without_symlink_components(notes_dir, artifact):
            continue
        note_type = frontmatter_note_type(artifact)
        if note_type in STANDALONE_NOTE_TYPES:
            return True
        text = artifact.read_text(encoding="utf-8", errors="replace")
        if "笔记侧独立体系" in text and "未确定的一一对应" in text:
            return True
    return False


def match_source_entries(ref: str, entries: list[SourceEntry]) -> list[SourceEntry]:
    ref_norm = normalize_path_text(ref)
    ref_text = normalize_text(ref_norm)
    matches: list[SourceEntry] = []
    for entry in entries:
        exact_candidates = {
            normalize_path_text(entry.root_relative),
            normalize_path_text(entry.course_relative),
            normalize_path_text(entry.name),
        }
        text_candidates = {normalize_text(candidate) for candidate in exact_candidates}
        if ref_norm in exact_candidates or ref_text in text_candidates:
            matches.append(entry)
    return matches


def check_source_mappings(
    source_root: Path,
    notes_root: Path,
    mapping: dict[str, str],
    include_ignored: bool = False,
    require_canonical_refs: bool = False,
) -> tuple[int, list[Path], list[SourceEntry], list[CoverageIssue]]:
    issues: list[CoverageIssue] = []
    mapped_notes_dirs: list[Path] = []
    entries: list[SourceEntry] = []
    total = 0
    for source_name, notes_name in mapping.items():
        source_dir = source_root / source_name
        notes_dir = notes_root / notes_name
        if not source_dir.is_dir():
            issues.append(CoverageIssue("missing_source_dir", source_dir, "mapped source directory does not exist"))
            continue
        if not notes_dir.is_dir():
            issues.append(CoverageIssue("missing_notes_dir", notes_dir, "mapped notes directory does not exist"))
            continue
        mapped_notes_dirs.append(notes_dir)
        local_entries = build_source_entries(source_root, source_name, include_ignored=include_ignored)
        entries.extend(local_entries)
        corpus = course_text(notes_dir)
        for entry in local_entries:
            total += 1
            if require_canonical_refs:
                present = source_entry_mentioned_exact(corpus, entry)
            else:
                normalized_corpus = normalize_text(corpus)
                candidates = (entry.course_relative, entry.root_relative, entry.name, entry.stem)
                present = any(
                    normalize_text(candidate) in normalized_corpus
                    for candidate in candidates
                )
            if not present:
                issues.append(
                    CoverageIssue(
                        "missing_source_mapping",
                        entry.path,
                        "source file name/stem is absent from source_manifest.md and 99_内容覆盖审查.md",
                    )
                )
    return total, mapped_notes_dirs, entries, issues


def source_entry_mentioned(text: str, entry: SourceEntry) -> bool:
    normalized = normalize_text(text)
    candidates = (entry.root_relative, entry.course_relative, entry.name, entry.stem)
    return any(normalize_text(candidate) in normalized for candidate in candidates)


def source_entry_mentioned_exact(text: str, entry: SourceEntry) -> bool:
    """Match only the canonical source-root-relative path."""

    canonical = normalize_path_text(entry.root_relative)
    haystack = normalize_path_text(text)
    # Do not let a longer path or a prefixed basename satisfy an exact-path
    # contract.  Markdown punctuation/backticks remain valid delimiters;
    # path-like characters and Unicode word characters do not.
    pattern = rf"(?<![\w/.-]){re.escape(canonical)}(?![\w/.-])"
    return re.search(pattern, haystack) is not None


def visible_source_references(text: str) -> list[tuple[int, str, str]]:
    """Return semantic backticked source refs outside block code and comments.

    Source paths intentionally use inline-code formatting. Temporarily remove
    only their backtick delimiters before applying the shared CommonMark code
    masker, so semantic inline refs stay visible while fenced, indented, list,
    and blockquote code remains excluded.
    """

    protected = list(text)
    for match in SOURCE_REF_RE.finditer(text):
        protected[match.start()] = " "
        protected[match.end() - 1] = " "
    visible = text_without_code("".join(protected))
    assert isinstance(visible, str)
    visible = COMMENT_SPAN_RE.sub(
        lambda match: "".join("\n" if char == "\n" else " " for char in match.group()),
        visible,
    )

    references: list[tuple[int, str, str]] = []
    offset = 0
    visible_lines = visible.splitlines(keepends=True)
    raw_lines = text.splitlines(keepends=True)
    assert len(raw_lines) == len(visible_lines)
    for line_number, (raw_line, visible_line) in enumerate(
        zip(raw_lines, visible_lines),
        start=1,
    ):
        for match in SOURCE_REF_RE.finditer(raw_line):
            start = offset + match.start(1)
            end = offset + match.end(1)
            if visible[start:end].strip():
                references.append((line_number, match.group(1), visible_line))
        offset += len(raw_line)
    return references


def page_source_evidence(text: str, entry: SourceEntry) -> bool:
    """Require canonical path and locator in the same evidence block or row."""

    return any(
        source_entry_mentioned_exact(block, entry)
        and bool(PAGE_EVIDENCE_RE.search(block))
        for block in source_evidence_blocks(text)
    )


def source_evidence_blocks(text: str) -> list[str]:
    """Split visible Markdown into paragraph/list/table evidence units.

    Blank lines delimit paragraphs, each list item starts a new unit, and each
    table row is independent. This prevents a canonical source on one row or
    paragraph from borrowing a page locator from an unrelated unit.
    """

    visible = text_without_code(text)
    assert isinstance(visible, str)
    raw_lines = text.splitlines()
    visible_lines = visible.splitlines()
    assert len(raw_lines) == len(visible_lines)
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            blocks.append("\n".join(current))
            current.clear()

    for raw_line, visible_line in zip(raw_lines, visible_lines):
        stripped = visible_line.strip()
        if not stripped:
            flush()
            continue
        raw_logical = re.sub(r"^(?:>\s*)+", "", raw_line.strip())
        is_table_row = "|" in raw_logical and (
            raw_logical.startswith("|") or raw_logical.endswith("|")
        )
        if is_table_row or EVIDENCE_HEADING_RE.match(raw_logical):
            flush()
            blocks.append(raw_line)
            continue
        if EVIDENCE_LIST_ITEM_RE.match(raw_logical):
            flush()
        current.append(raw_line)
    flush()
    return blocks


def resolve_note_target(notes_dir: Path, raw_target: str) -> Path | None:
    target = normalize_path_text(raw_target).strip()
    if not target or has_cross_platform_anchor(target):
        return None
    candidates: list[Path] = []
    target_path = Path(target)
    stripped = target
    prefix = notes_dir.name + "/"
    if stripped.startswith(prefix):
        stripped = stripped[len(prefix) :]
    notes_root = notes_dir.parent
    for candidate in (notes_dir / target_path, notes_dir / stripped, notes_root / target_path):
        if candidate.suffix.lower() != ".md":
            candidate = candidate.with_suffix(".md")
        try:
            relative = candidate.relative_to(notes_root)
        except ValueError:
            continue
        # Reject lexical traversal before looking at filesystem resolution, and
        # accept only a regular note reached without root/ancestor/leaf
        # symlinks.  A directory named ``owner.md`` is not a Markdown note.
        if any(component in {"", ".", ".."} for component in relative.parts):
            continue
        if (
            is_within_root(notes_root, candidate)
            and is_regular_file_without_symlink_components(notes_root, candidate)
            and candidate not in candidates
        ):
            candidates.append(candidate)
    return candidates[0] if candidates else None


def source_family_signature(value: str) -> str:
    """Normalize lecture parts so adjacent files can be reported together."""

    stem = Path(value).stem
    stem = normalize_text(stem).replace(" ", "_")
    stem = re.sub(r"(?:[_-](?:part|pt)[_-]?\d+)$", "", stem)
    return stem


def adjacent_source_entries(entry: SourceEntry, entries: list[SourceEntry]) -> list[SourceEntry]:
    family = source_family_signature(entry.stem)
    if not family:
        return []
    return [
        other
        for other in entries
        if other != entry
        and other.course_name == entry.course_name
        and source_family_signature(other.stem) == family
    ]


BODY_OWNER_MARKERS = ("来源：", "来源:", "对应源资料", "对应源课件", "源资料：", "源课件：")


def body_source_owner_conflicts(
    text: str,
    entries: list[SourceEntry],
    allowed_root_paths: set[str],
) -> list[tuple[str, str]]:
    """Find explicit body owner lines that name another canonical source.

    A source stem, note filename, or numeric ordinal is never used here.
    Only a canonical backticked source path on an owner-labelled body line
    can contradict the manifest/frontmatter owner.  Other prose remains
    outside the automatic owner decision and may require manual review.
    """

    allowed = {normalize_path_text(path) for path in allowed_root_paths}
    exact_entries = {normalize_path_text(entry.root_relative): entry for entry in entries}
    conflicts: list[tuple[str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not any(marker in line for marker in BODY_OWNER_MARKERS):
            continue
        for raw in SOURCE_REF_RE.findall(line):
            normalized = normalize_path_text(raw)
            entry = exact_entries.get(normalized)
            if entry is not None and normalized not in allowed:
                conflicts.append((str(line_number), entry.root_relative))
    return conflicts


def check_four_way_source_coverage(
    source_root: Path,
    notes_root: Path,
    mapping: dict[str, str],
    entries: list[SourceEntry],
    include_ignored: bool = False,
    require_adjacent_source_evidence: bool = False,
) -> list[CoverageIssue]:
    """Check source existence, manifest, audit, and note ownership separately."""

    issues: list[CoverageIssue] = []
    for source_name, notes_name in mapping.items():
        notes_dir = notes_root / notes_name
        local_entries = [entry for entry in entries if entry.course_name == source_name]
        if not notes_dir.is_dir():
            continue
        manifest_path = notes_dir / "source_manifest.md"
        audit_path = notes_dir / "99_内容覆盖审查.md"
        manifest_safe = is_regular_file_without_symlink_components(notes_dir, manifest_path)
        audit_safe = is_regular_file_without_symlink_components(notes_dir, audit_path)
        manifest_text = manifest_path.read_text(encoding="utf-8", errors="replace") if manifest_safe else ""
        audit_text = audit_path.read_text(encoding="utf-8", errors="replace") if audit_safe else ""
        if not manifest_path.exists() and not manifest_path.is_symlink():
            issues.append(CoverageIssue("missing_source_manifest", notes_dir, "mapped notes directory has no source_manifest.md"))
        if not audit_path.exists() and not audit_path.is_symlink():
            issues.append(CoverageIssue("missing_coverage_audit", notes_dir, "mapped notes directory has no 99_内容覆盖审查.md"))

        note_paths = [
            path
            for path in markdown_files(notes_dir, include_ignored=include_ignored)
            if path.name not in {"source_manifest.md", "99_内容覆盖审查.md"}
        ]
        for entry in local_entries:
            if manifest_safe and not source_entry_mentioned_exact(manifest_text, entry):
                issues.append(
                    CoverageIssue(
                        "missing_source_manifest_mapping",
                        manifest_path,
                        f"canonical source {entry.root_relative!r} is absent from source_manifest.md",
                    )
                )
            if audit_safe and not source_entry_mentioned_exact(audit_text, entry):
                issues.append(
                    CoverageIssue(
                        "missing_coverage_audit_mapping",
                        audit_path,
                        f"canonical source {entry.root_relative!r} is absent from 99_内容覆盖审查.md",
                    )
                )

            frontmatter_paths: list[Path] = []
            body_paths: list[Path] = []
            for path in note_paths:
                body = note_body_text(path)
                declared = frontmatter_source_files(path)
                if any(normalize_path_text(item) == normalize_path_text(entry.root_relative) for item in declared):
                    frontmatter_paths.append(path)
                if page_source_evidence(body, entry):
                    body_paths.append(path)

            if frontmatter_paths and not body_paths:
                for path in frontmatter_paths:
                    issues.append(
                        CoverageIssue(
                            "missing_body_source_evidence",
                            path,
                            f"frontmatter declares {entry.root_relative!r} but body lacks canonical path and page/slide evidence",
                            status="MANUAL_REVIEW_REQUIRED",
                        )
                    )
            if body_paths and not frontmatter_paths:
                for path in body_paths:
                    issues.append(
                        CoverageIssue(
                            "missing_frontmatter_source_evidence",
                            path,
                            f"body cites {entry.root_relative!r} with page/slide evidence but frontmatter omits the canonical source",
                        )
                    )

            owner_paths = sorted(set(frontmatter_paths + body_paths))
            if not owner_paths:
                siblings = adjacent_source_entries(entry, local_entries)
                kind = "missing_adjacent_source_evidence" if require_adjacent_source_evidence and siblings else "missing_note_source_ownership"
                detail = f"source {entry.root_relative!r} has no owning note frontmatter/body evidence"
                if siblings:
                    detail += "; adjacent files: " + ", ".join(other.root_relative for other in siblings)
                issues.append(
                    CoverageIssue(
                        kind,
                        notes_dir,
                        detail,
                        status="MANUAL_REVIEW_REQUIRED" if kind == "missing_adjacent_source_evidence" else "STRUCTURAL",
                    )
                )
    return issues


def check_source_dir_reconciliation(
    source_root: Path,
    notes_root: Path,
    mapping: dict[str, str],
    include_ignored: bool = False,
) -> list[CoverageIssue]:
    """Report source directories and manifest-bearing note directories omitted from mapping."""

    issues: list[CoverageIssue] = []
    if not source_root.is_dir() or not notes_root.is_dir():
        return issues
    source_dirs = {
        path.name
        for path in source_root.iterdir()
        if path.is_dir()
        and (include_ignored or path.name not in DEFAULT_IGNORED_DIR_NAMES)
        and source_files(path, include_ignored=include_ignored)
    }
    for source_name in sorted(source_dirs - set(mapping)):
        issues.append(
            CoverageIssue(
                "unmapped_source_dir",
                source_root / source_name,
                "source directory contains PPT/PDF files but is not present in --mapping",
            )
        )
    mapped_note_dirs = {str((notes_root / notes_name).resolve()) for notes_name in mapping.values()}
    for notes_dir in sorted(path for path in notes_root.iterdir() if path.is_dir()):
        if notes_dir.name in DEFAULT_IGNORED_DIR_NAMES:
            continue
        if is_standalone_notes_dir(notes_dir):
            continue
        if not any(
            is_regular_file_without_symlink_components(notes_dir, notes_dir / name)
            for name in FIXED_NOTES_ARTIFACT_NAMES
        ):
            continue
        if str(notes_dir.resolve()) not in mapped_note_dirs:
            issues.append(
                CoverageIssue(
                    "unmapped_notes_dir",
                    notes_dir,
                    "notes directory has source coverage artifacts but is not present in --mapping",
                )
            )
    return issues


def check_paper_source_ownership(
    notes_root: Path,
    source_root: Path,
    include_ignored: bool = False,
) -> list[CoverageIssue]:
    """Ensure paper notes declare existing local source files and cite declared ownership."""

    issues: list[CoverageIssue] = []
    if not source_root.is_dir():
        return issues
    for path in markdown_files(notes_root, include_ignored=include_ignored):
        if path.name in {"source_manifest.md", "99_内容覆盖审查.md"} or path.parent.name == "模板":
            continue
        if frontmatter_note_type(path) != "paper_note":
            continue
        declared = frontmatter_source_files(path)
        if not declared:
            issues.append(CoverageIssue("paper_source_missing", path, "paper_note has no non-empty source_files declaration"))
            continue
        declared_matches = {normalize_path_text(item) for item in declared}
        for raw in declared:
            if is_external_source_ref(raw):
                continue
            source_path = resolve_beneath(source_root, raw)
            if source_path is None:
                issues.append(
                    CoverageIssue(
                        "paper_source_outside_root",
                        path,
                        f"declared paper source escapes source root: {raw!r}",
                    )
                )
                continue
            lexical_source = source_root / Path(normalize_path_text(raw))
            if not lexical_source.exists() and not lexical_source.is_symlink():
                issues.append(CoverageIssue("paper_source_not_found", path, f"declared paper source does not exist: {raw!r}"))
            elif exact_regular_source_target(source_root, raw) is None:
                issues.append(
                    CoverageIssue(
                        "paper_source_not_regular",
                        path,
                        f"declared paper source must use exact spelling and be a regular file without symlink components: {raw!r}",
                    )
                )
        body = note_body_text(path)
        for raw in SOURCE_REF_RE.findall(body):
            if is_external_source_ref(raw):
                continue
            raw_norm = normalize_path_text(raw)
            if resolve_beneath(source_root, raw_norm) is None:
                issues.append(
                    CoverageIssue(
                        "paper_source_outside_root",
                        path,
                        f"body cites source outside source root: {raw!r}",
                    )
                )
                continue
            lexical_source = source_root / Path(raw_norm)
            if not lexical_source.exists() and not lexical_source.is_symlink():
                issues.append(
                    CoverageIssue(
                        "paper_source_not_found",
                        path,
                        f"body cites local source that does not exist: {raw!r}",
                    )
                )
                continue
            if exact_regular_source_target(source_root, raw_norm) is None:
                issues.append(
                    CoverageIssue(
                        "paper_source_not_regular",
                        path,
                        f"body source must use exact spelling and be a regular file without symlink components: {raw!r}",
                    )
                )
                continue
            all_source_entries = [
                SourceEntry(
                    course_name="",
                    path=source_path,
                    course_relative=source_path.relative_to(source_root).as_posix(),
                    root_relative=source_path.relative_to(source_root).as_posix(),
                    name=source_path.name,
                    stem=source_path.stem,
                )
                for source_path in source_files(source_root, include_ignored=include_ignored)
            ]
            basename_matches = [entry for entry in all_source_entries if entry.name == Path(raw_norm).name]
            declared_basename_matches = [
                declared_ref
                for declared_ref in declared_matches
                if Path(declared_ref).name == Path(raw_norm).name
            ]
            if raw_norm not in declared_matches and len(basename_matches) > 1:
                issues.append(
                    CoverageIssue(
                        "paper_source_ambiguous_basename",
                        path,
                        f"body cites basename {Path(raw_norm).name!r}, which resolves to multiple source paths",
                    )
                )
                continue
            if raw_norm not in declared_matches and len(declared_basename_matches) != 1:
                issues.append(
                    CoverageIssue(
                        "paper_source_not_declared",
                        path,
                        f"body cites local source {raw!r} but it is absent from frontmatter source_files",
                    )
                )
    return issues


def is_external_source_ref(value: str) -> bool:
    return normalize_path_text(value).startswith(("http://", "https://", "mailto:"))


def has_generated_source_marker(text: str) -> bool:
    candidate = NEGATED_SELF_WRITTEN_RE.sub("", text)
    if EXPLICIT_SELF_WRITTEN_LABEL_RE.search(candidate):
        return True
    if any(marker in candidate for marker in GENERATED_MARKERS):
        return True
    return any(pattern.search(candidate) for pattern in NATURAL_GENERATED_SOURCE_PATTERNS)


def split_markdown_table_row(line: str) -> list[str]:
    """Split a Markdown table row with optional outer pipes."""

    text = line.strip()
    if "|" not in text:
        return []
    cells: list[str] = []
    current: list[str] = []
    backslash_run = 0
    saw_separator = False
    for char in text:
        if char == "|" and backslash_run % 2 == 0:
            cells.append("".join(current).strip())
            current = []
            backslash_run = 0
            saw_separator = True
            continue
        current.append(char)
        backslash_run = backslash_run + 1 if char == "\\" else 0
    cells.append("".join(current).strip())
    if not saw_separator:
        return []
    if text.startswith("|") and cells and not cells[0]:
        cells.pop(0)
    if text.endswith("|") and cells and not cells[-1]:
        cells.pop()
    return cells if len(cells) >= 2 else []


def example_source_column(cells: list[str]) -> int | None:
    headers = [re.sub(r"\s+", "", cell).strip("`*_ ") for cell in cells]
    matches = [
        index
        for index, header in enumerate(headers)
        if re.fullmatch(r"(?:资料)?来源(?:说明|文件)?|源(?:资料|文件)", header)
    ]
    has_topic = any(header in {"知识点", "主题", "考点"} for header in headers)
    has_explanation = any(
        re.fullmatch(
            r"(?:例题(?:[/／]辅助题)?|辅助题)(?:[与及](?:详细)?(?:解析|解答))?",
            header,
        )
        for header in headers
    )
    return matches[0] if len(matches) == 1 and has_topic and has_explanation else None


def table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells
    )


def has_source_or_generated_example(line: str) -> bool:
    has_source_label = any(label in line for label in SOURCE_EXAMPLE_LABELS)
    has_source_example = has_source_label and (
        "（/" in line
        or "来源：`" in line
        or "源资料：`" in line
        or line.lstrip().startswith("#")
    )
    has_generated_example = has_generated_source_marker(line) and (
        "补充题（/" in line or "来源说明" in line or line.lstrip().startswith("#")
    )
    return has_source_example or has_generated_example


def check_example_evidence(
    notes_dirs: list[Path], include_ignored: bool = False
) -> tuple[int, int, int, int, list[CoverageIssue]]:
    supplement_notes = 0
    supplement_bullets = 0
    source_example_lines = 0
    generated_lines = 0
    issues: list[CoverageIssue] = []

    for notes_dir in sorted({path.resolve() for path in notes_dirs}):
        local_source_examples = 0
        local_generated_lines = 0
        files = [
            path
            for path in markdown_files(notes_dir, include_ignored=include_ignored)
            if path.name not in {"source_manifest.md", "99_内容覆盖审查.md"}
        ]
        explicit_no_example_boundary = any(
            marker in course_text(notes_dir) and "例题" in course_text(notes_dir)
            for marker in NO_EXAMPLE_DECLARATIONS
        )
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            in_supplement = False
            in_example_table = False
            source_column: int | None = None
            example_header_checked = False
            if SUPPLEMENT_HEADING in text:
                supplement_notes += 1
            for line_number, line in enumerate(text.splitlines(), start=1):
                if line.startswith("## "):
                    in_supplement = line.strip() == SUPPLEMENT_HEADING
                    in_example_table = line.strip() == EXAMPLE_HEADING
                    source_column = None
                    example_header_checked = False
                    continue
                generated_line = has_generated_source_marker(line)
                table_cells = split_markdown_table_row(line) if in_example_table else []
                if (
                    table_cells
                    and not table_separator(table_cells)
                    and not example_header_checked
                ):
                    example_header_checked = True
                    source_column = example_source_column(table_cells)
                table_source_cell = ""
                if (
                    source_column is not None
                    and not table_separator(table_cells)
                    and source_column < len(table_cells)
                ):
                    table_source_cell = table_cells[source_column]
                    generated_line = generated_line or has_generated_source_marker(table_source_cell)
                table_source_line = (
                    not generated_line
                    and in_example_table
                    and SOURCE_FILE_MARKER_RE.search(table_source_cell) is not None
                )
                if in_supplement and line.startswith("- "):
                    supplement_bullets += 1
                    if "来源：`" not in line or "页/slide：" not in line or "主题：" not in line:
                        issues.append(CoverageIssue("bad_supplement_fields", path, f"line {line_number} lacks source/page/topic fields"))
                    if not has_source_or_generated_example(line):
                        issues.append(CoverageIssue("bad_supplement_example", path, f"line {line_number} lacks source example or generated-question evidence"))

                if not generated_line and (
                    table_source_line
                    or any(label in line for label in SOURCE_EXAMPLE_LABELS)
                ):
                    source_example_lines += 1
                    local_source_examples += 1
                    if (
                        not line.lstrip().startswith("#")
                        and "（/" not in line
                        and "源资料：`" not in line
                        and not table_source_line
                    ):
                        issues.append(
                            CoverageIssue(
                                "bad_source_example",
                                path,
                                f"line {line_number} lacks a traceable source marker",
                                status="MANUAL_REVIEW_REQUIRED",
                            )
                        )
                if generated_line or "生成辅助题" in line or "补充题（/" in line:
                    generated_lines += 1
                    local_generated_lines += 1
                    if not generated_line and not any(label in line for label in SOURCE_EXAMPLE_LABELS):
                        issues.append(
                            CoverageIssue(
                                "bad_generated_example",
                                path,
                                f"line {line_number} lacks generated-question source rationale",
                                status="MANUAL_REVIEW_REQUIRED",
                            )
                        )
                if any(marker in line for marker in RESIDUAL_REVIEW_MARKERS):
                    issues.append(
                        CoverageIssue(
                            "residual_manual_review_marker",
                            path,
                            f"line {line_number} still contains a manual-review marker",
                            status="MANUAL_REVIEW_REQUIRED",
                        )
                    )
        if (
            files
            and local_source_examples + local_generated_lines == 0
            and not explicit_no_example_boundary
        ):
            issues.append(
                CoverageIssue(
                    "no_example_evidence",
                    notes_dir,
                    "mapped notes directory has no source-derived or generated example evidence",
                    status="MANUAL_REVIEW_REQUIRED",
                )
            )

    return supplement_notes, supplement_bullets, source_example_lines, generated_lines, issues


def check_text_hygiene(notes_dirs: list[Path], include_ignored: bool = False) -> list[CoverageIssue]:
    issues: list[CoverageIssue] = []
    for notes_dir in sorted({path.resolve() for path in notes_dirs}):
        for path in markdown_files(notes_dir, include_ignored=include_ignored):
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                bad_controls = [
                    char
                    for char in line
                    if (ord(char) < 32 and char not in "\t") or ord(char) == 127
                ]
                if bad_controls:
                    issues.append(CoverageIssue("control_character", path, f"line {line_number} contains hidden control characters"))
                    break
                for label in LEGACY_FAILURE_LABELS:
                    if label in line:
                        issues.append(CoverageIssue("legacy_failure_label", path, f"line {line_number} still contains {label!r}"))
    return issues


def check_audit_source_tables(
    notes_dirs: list[Path],
    entries: list[SourceEntry],
    require_course_prefixed_refs: bool,
    include_ignored: bool = False,
) -> list[CoverageIssue]:
    issues: list[CoverageIssue] = []
    audit_names = ("source_manifest.md", "99_内容覆盖审查.md")
    for notes_dir in sorted({path.resolve() for path in notes_dirs}):
        for audit_name in audit_names:
            path = notes_dir / audit_name
            if not is_regular_file_without_symlink_components(notes_dir, path):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_number, ref, visible_line in visible_source_references(text):
                if is_external_source_ref(ref):
                    continue
                links = WIKI_LINK_RE.findall(visible_line)
                matches = match_source_entries(ref, entries)
                if not matches:
                    issues.append(
                        CoverageIssue(
                            "unknown_source_ref",
                            path,
                            f"line {line_number} cites no exact regular source in the selected source root: {ref!r}",
                        )
                    )
                    continue
                if len(matches) > 1:
                    issues.append(
                        CoverageIssue(
                            "ambiguous_source_ref",
                            path,
                            f"line {line_number} uses non-unique source reference {ref!r}; use the canonical root-relative path",
                        )
                    )
                for entry in matches:
                    if require_course_prefixed_refs and entry.course_name not in ("", "."):
                        ref_norm = normalize_path_text(ref)
                        root_norm = normalize_path_text(entry.root_relative)
                        if ref_norm != root_norm:
                            issues.append(
                                CoverageIssue(
                                    "noncanonical_source_ref",
                                    path,
                                    f"line {line_number} uses {ref!r}; prefer root-relative source path {entry.root_relative!r}",
                                )
                            )
                    for link in links:
                        target_path = resolve_note_target(notes_dir, link)
                        if target_path is None:
                            issues.append(
                                CoverageIssue(
                                    "manifest_target_missing_note",
                                    path,
                                    f"line {line_number} targets missing note {link!r} for canonical source {entry.root_relative!r}",
                                )
                            )
                            continue
                        declared_refs = frontmatter_source_files(target_path)
                        declared_owner = any(
                            normalize_path_text(raw) == normalize_path_text(entry.root_relative)
                            for raw in declared_refs
                        )
                        if not declared_owner:
                            issues.append(
                                CoverageIssue(
                                    "manifest_target_owner_mismatch",
                                    path,
                                    f"line {line_number} targets {link!r}, but that note lacks canonical frontmatter ownership for {entry.root_relative!r}",
                                )
                            )
                        allowed_refs = set(declared_refs)
                        allowed_refs.add(entry.root_relative)
                        body_conflicts = body_source_owner_conflicts(
                            note_body_text(target_path),
                            entries,
                            allowed_refs,
                        )
                        for body_line, conflicting_source in body_conflicts:
                            issues.append(
                                CoverageIssue(
                                    "body_source_owner_mismatch",
                                    target_path,
                                    f"body line {body_line} names canonical source {conflicting_source!r}, conflicting with manifest/frontmatter owner {entry.root_relative!r}",
                                )
                            )
    return issues


def check_note_source_ownership(
    notes_dirs: list[Path], entries: list[SourceEntry], include_ignored: bool = False
) -> list[CoverageIssue]:
    issues: list[CoverageIssue] = []
    skip_names = {"source_manifest.md", "99_内容覆盖审查.md"}
    for notes_dir in sorted({path.resolve() for path in notes_dirs}):
        for path in markdown_files(notes_dir, include_ignored=include_ignored):
            if path.name in skip_names:
                continue
            declared_refs = frontmatter_source_files(path)
            if not declared_refs:
                continue
            allowed_refs = set(declared_refs)
            for line_number, conflicting_source in body_source_owner_conflicts(
                note_body_text(path),
                entries,
                allowed_refs,
            ):
                issues.append(
                    CoverageIssue(
                        "body_source_owner_mismatch",
                        path,
                        f"body line {line_number} names canonical source {conflicting_source!r}, absent from frontmatter source_files",
                    )
                )
    return issues


def main() -> int:
    configure_output_encoding()
    parser = argparse.ArgumentParser(description="Check PPT/PDF source coverage evidence in Obsidian notes.")
    parser.add_argument("--source-root", type=Path, required=True, help="Root directory containing PPT/PDF source folders")
    parser.add_argument("--notes-root", type=Path, required=True, help="Root directory containing Obsidian notes")
    parser.add_argument(
        "--mapping",
        type=parse_mapping,
        required=False,
        help="Comma-separated source=notes directory mapping, for example '课程源=课程笔记,raw=notes'",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable four-way coverage, sibling discovery, source-dir reconciliation, paper ownership, and adjacent-source checks",
    )
    parser.add_argument(
        "--discover-sibling-mappings",
        action="store_true",
        help="Add source directories whose note directory has the same name (for example cs231n)",
    )
    parser.add_argument(
        "--require-four-way-source-coverage",
        action="store_true",
        help="Require separate evidence in source_manifest.md, 99_内容覆盖审查.md, and an owning note",
    )
    parser.add_argument(
        "--require-adjacent-source-evidence",
        action="store_true",
        help="Report missing ownership for adjacent source parts such as lecture_1_part_1/2",
    )
    parser.add_argument(
        "--require-source-dir-reconciliation",
        action="store_true",
        help="Report source directories and manifest-bearing note directories omitted from --mapping",
    )
    parser.add_argument(
        "--check-paper-source-ownership",
        action="store_true",
        help="Check paper_note source_files declarations and local source existence",
    )
    parser.add_argument(
        "--include-ignored",
        action="store_true",
        help="Include default-ignored cache, generated, and scripts directories in scans",
    )
    parser.add_argument(
        "--require-course-prefixed-source-refs",
        action="store_true",
        help="Require source refs in source_manifest.md and 99_内容覆盖审查.md to use root-relative paths such as 课程/ch1.pdf",
    )
    args = parser.parse_args()

    if not args.mapping and not (
        args.discover_sibling_mappings or args.strict or args.check_paper_source_ownership
    ):
        parser.error(
            "--mapping is required unless --discover-sibling-mappings, --strict, or paper-only checking is used"
        )

    source_root, source_root_issue = validate_scan_root(args.source_root, "--source-root")
    notes_root, notes_root_issue = validate_scan_root(args.notes_root, "--notes-root")
    root_issues = [
        issue for issue in (source_root_issue, notes_root_issue) if issue is not None
    ]
    if root_issues:
        for issue in root_issues:
            print(f"{issue.status}: {issue.kind.upper()}: {issue.path}: {issue.message}")
        return 1
    assert source_root is not None and notes_root is not None
    discover = args.discover_sibling_mappings or args.strict
    mapping, mapping_path_issues = validate_mapping_paths(
        source_root,
        notes_root,
        args.mapping or {},
    )
    if (
        not mapping_path_issues
        and not any(source_root.iterdir())
        and not any(notes_root.iterdir())
    ):
        print(
            "STRUCTURAL: EMPTY_COVERAGE_SCOPE: .: source and notes roots are both "
            "empty; there is no coverage scope to validate"
        )
        return 1
    if discover:
        mapping = discover_sibling_mappings(
            source_root,
            notes_root,
            mapping,
            include_ignored=args.include_ignored,
        )
    total_sources, mapped_notes_dirs, entries, mapping_check_issues = check_source_mappings(
        source_root,
        notes_root,
        mapping,
        include_ignored=args.include_ignored,
        require_canonical_refs=args.strict or args.require_course_prefixed_source_refs,
    )
    supplement_notes, supplement_bullets, source_examples, generated_lines, evidence_issues = check_example_evidence(
        mapped_notes_dirs,
        include_ignored=args.include_ignored,
    )
    hygiene_issues = check_text_hygiene(mapped_notes_dirs, include_ignored=args.include_ignored)
    audit_issues = check_audit_source_tables(
        mapped_notes_dirs,
        entries,
        args.strict or args.require_course_prefixed_source_refs,
        include_ignored=args.include_ignored,
    )
    ownership_issues = check_note_source_ownership(
        mapped_notes_dirs,
        entries,
        include_ignored=args.include_ignored,
    )
    four_way_issues = []
    if args.require_four_way_source_coverage or args.strict:
        four_way_issues = check_four_way_source_coverage(
            source_root,
            notes_root,
            mapping,
            entries,
            include_ignored=args.include_ignored,
            require_adjacent_source_evidence=args.require_adjacent_source_evidence or args.strict,
        )
    reconciliation_issues = []
    if args.require_source_dir_reconciliation or args.strict:
        reconciliation_issues = check_source_dir_reconciliation(
            source_root,
            notes_root,
            mapping,
            include_ignored=args.include_ignored,
        )
    paper_issues = []
    if args.check_paper_source_ownership or args.strict:
        paper_issues = check_paper_source_ownership(
            notes_root,
            source_root,
            include_ignored=args.include_ignored,
        )
    boundary_issues = source_boundary_issues(source_root, include_ignored=args.include_ignored)
    boundary_issues += notes_artifact_boundary_issues(
        notes_root,
        include_ignored=args.include_ignored,
    )
    mapping_issues = mapping_path_issues + mapping_check_issues + boundary_issues
    issues = (
        mapping_issues
        + evidence_issues
        + hygiene_issues
        + audit_issues
        + ownership_issues
        + four_way_issues
        + reconciliation_issues
        + paper_issues
    )

    print(f"course_source_files {total_sources}")
    print(f"missing_source_mappings {len(mapping_issues)}")
    print(f"supplement_index_notes {supplement_notes}")
    print(f"supplement_bullets {supplement_bullets}")
    print(f"source_example_lines {source_examples}")
    print(f"generated_lines {generated_lines}")
    print(f"text_hygiene_issues {len(hygiene_issues)}")
    print(f"source_table_issues {len(audit_issues)}")
    print(f"note_source_ownership_issues {len(ownership_issues)}")
    print(f"four_way_source_issues {len(four_way_issues)}")
    print(f"source_dir_reconciliation_issues {len(reconciliation_issues)}")
    print(f"paper_source_ownership_issues {len(paper_issues)}")
    print(f"coverage_evidence_issues {len(issues)}")
    structural_issues = [issue for issue in issues if issue.status == "STRUCTURAL"]
    manual_review_issues = [issue for issue in issues if issue.status == "MANUAL_REVIEW_REQUIRED"]
    print(f"structural_issues {len(structural_issues)}")
    print(f"manual_review_required_issues {len(manual_review_issues)}")
    for issue in issues:
        try:
            display_path = issue.path.relative_to(notes_root)
        except ValueError:
            try:
                display_path = issue.path.relative_to(source_root)
            except ValueError:
                display_path = issue.path
        print(f"{issue.status}: {issue.kind.upper()}: {display_path}: {issue.message}")

    # Keep the strict gate conservative: manual-review findings are not a
    # pass or a claim of full coverage, and therefore remain non-zero until a
    # human closes them.  The status prefix distinguishes them from concrete
    # canonical-path/owner/manifest failures that are directly actionable.
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
