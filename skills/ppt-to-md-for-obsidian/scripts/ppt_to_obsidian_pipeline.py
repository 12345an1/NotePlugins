#!/usr/bin/env python3
"""Run a deterministic source-to-cleaned-Markdown pipeline.

The script does not attempt to write final study notes. It creates reproducible
raw extraction artifacts and a manifest that tells Codex or a human editor what
to rewrite into Obsidian notes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import stat
import sys
import unicodedata

try:
    from .clean_latex_from_ppt import clean_text
    from .extract_legacy_ppt_text import LegacyPptTextResult, extract_legacy_ppt_text
    from .extract_pptx_text import extract_pptx_result
    from .extract_pdf_text import LOW_COVERAGE_WARNING, extract_pdf_result
    from .safe_io import (
        ensure_safe_directory as ensure_nonsymlink_directory,
        ensure_safe_input_directory,
        ensure_safe_input_file,
        safe_write_text,
    )
except ImportError:
    from clean_latex_from_ppt import clean_text
    from extract_legacy_ppt_text import LegacyPptTextResult, extract_legacy_ppt_text
    from extract_pptx_text import extract_pptx_result
    from extract_pdf_text import LOW_COVERAGE_WARNING, extract_pdf_result
    from safe_io import (
        ensure_safe_directory as ensure_nonsymlink_directory,
        ensure_safe_input_directory,
        ensure_safe_input_file,
        safe_write_text,
    )


SUPPORTED_SUFFIXES = {".ppt", ".pptx", ".pdf"}


class PipelineConfigError(ValueError):
    """Stable user-facing failure for an unreadable or malformed config."""

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


@dataclass
class PipelineConfig:
    source: Path
    output_dir: Path
    mode: str = "course-notes"
    unicode_math: bool = False
    soffice: str | None = None
    converted_dir: str = "converted_pptx"
    course_name: str = "Course"
    overview_name: str = "00_课程总览.md"
    create_review_placeholders: bool = True


@dataclass
class ExtractionResult:
    actual_source: Path
    text: str
    backend: str
    partial: bool = False
    notes: list[str] | None = None
    text_record_count: int | None = None
    low_coverage: bool = False
    empty_pages: int | None = None
    char_count: int | None = None
    page_count: int | None = None
    slide_count: int | None = None
    blank_slides: int | None = None
    media_objects: int | None = None


@dataclass
class ProcessedSource:
    source: Path
    raw: Path
    cleaned: Path
    backend: str
    partial: bool = False
    notes: list[str] | None = None
    text_record_count: int | None = None
    low_coverage: bool = False
    empty_pages: int | None = None
    char_count: int | None = None
    page_count: int | None = None
    slide_count: int | None = None
    blank_slides: int | None = None
    media_objects: int | None = None


def safe_relative_config_path(value: str, label: str) -> Path:
    """Return a root-relative configured child path without traversal."""

    try:
        path = Path(value)
        windows_path = PureWindowsPath(value)
    except TypeError as exc:
        raise ValueError(f"{label} must be a relative path string") from exc
    if path.is_absolute() or windows_path.drive or windows_path.anchor:
        raise ValueError(f"{label} must be root-relative, not absolute: {value!r}")
    if not path.parts or path == Path(".") or ".." in path.parts or ".." in windows_path.parts:
        raise ValueError(f"{label} must not be empty or contain '..': {value!r}")
    return path


def relative_beneath(root: Path, path: Path) -> Path:
    root = Path(os.path.abspath(root.expanduser()))
    candidate = path if path.is_absolute() else root / path
    candidate = Path(os.path.abspath(candidate))
    try:
        return candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"output path escapes output_dir: {path}") from exc


def ensure_safe_directory(root: Path, path: Path, *, create: bool) -> Path:
    """Validate or create a directory without traversing symlink components."""

    root = Path(os.path.abspath(root.expanduser()))
    relative = relative_beneath(root, path)
    current = root
    for component in relative.parts:
        current = current / component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if not create:
                break
            current.mkdir()
            mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValueError(f"output directory contains symlink component: {current}")
        if not stat.S_ISDIR(mode):
            raise ValueError(f"output directory component is not a directory: {current}")
    return root / relative


def ensure_safe_output_path(root: Path, path: Path, *, create_parent: bool = True) -> Path:
    root = root.resolve()
    relative = relative_beneath(root, path)
    candidate = root / relative
    ensure_safe_directory(root, candidate.parent, create=create_parent)
    try:
        mode = candidate.lstat().st_mode
    except FileNotFoundError:
        return candidate
    if stat.S_ISLNK(mode):
        raise ValueError(f"output path is a symlink: {candidate}")
    if not stat.S_ISREG(mode):
        raise ValueError(f"output path is not a regular file: {candidate}")
    return candidate


def write_text_no_follow(root: Path, path: Path, content: str) -> None:
    candidate = ensure_safe_output_path(root, path)
    safe_write_text(candidate, content)


def load_yaml_config(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        raise PipelineConfigError(path, "PyYAML is required to read config files") from None

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        raise PipelineConfigError(path, "config file does not exist") from None
    except OSError:
        raise PipelineConfigError(path, "config path cannot be inspected") from None
    if not stat.S_ISREG(mode):
        raise PipelineConfigError(path, "config path is not a regular file")
    try:
        safe_path = ensure_safe_input_file(path)
    except (OSError, ValueError):
        raise PipelineConfigError(
            path,
            "config path must not contain symlink components",
        ) from None
    try:
        text = safe_path.read_text(encoding="utf-8")
    except UnicodeError:
        raise PipelineConfigError(path, "config file must be valid UTF-8") from None
    except OSError:
        raise PipelineConfigError(path, "config file cannot be read") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise PipelineConfigError(path, "config contains invalid YAML") from None
    if not isinstance(data, dict):
        raise PipelineConfigError(path, "config must be a YAML mapping")
    return data


def config_mapping_section(data: dict, path: Path, name: str) -> dict:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise PipelineConfigError(
            path,
            f"config section {name} must be a YAML mapping",
        )
    return value


def config_from_args(args: argparse.Namespace) -> PipelineConfig:
    if args.config:
        data = load_yaml_config(args.config)
        clean = config_mapping_section(data, args.config, "clean")
        conversion = config_mapping_section(data, args.config, "conversion")
        obsidian = config_mapping_section(data, args.config, "obsidian")
    else:
        data = {}
        clean = {}
        conversion = {}
        obsidian = {}

    source = Path(args.source or data.get("source", "."))
    output_dir = Path(args.output_dir or data.get("output_dir", "build/obsidian-pipeline"))
    mode = args.mode or data.get("mode", "course-notes")

    return PipelineConfig(
        source=source,
        output_dir=output_dir,
        mode=mode,
        unicode_math=args.unicode_math or bool(clean.get("unicode_math", False)),
        soffice=args.soffice or conversion.get("soffice"),
        converted_dir=conversion.get("converted_dir", "converted_pptx"),
        course_name=obsidian.get("course_name", source.stem or "Course"),
        overview_name=obsidian.get("overview_name", "00_课程总览.md"),
        create_review_placeholders=bool(obsidian.get("create_review_placeholders", True)),
    )


def path_is_beneath(path: Path, root: Path) -> bool:
    candidate = Path(os.path.abspath(path.expanduser()))
    root = Path(os.path.abspath(root.expanduser()))
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def iter_sources(
    source: Path,
    *,
    exclude_roots: tuple[Path, ...] = (),
) -> list[Path]:
    """Enumerate regular source files without following any symlink."""

    source = source.expanduser()
    try:
        source_mode = source.lstat().st_mode
    except FileNotFoundError:
        raise ValueError(f"source does not exist: {source}") from None
    if stat.S_ISLNK(source_mode):
        raise ValueError(f"source path is a symlink: {source}")

    if stat.S_ISREG(source_mode):
        safe_source = ensure_safe_input_file(source)
        if safe_source.suffix.lower() not in SUPPORTED_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
            raise ValueError(f"unsupported explicit source type: {source}; expected one of {supported}")
        return [safe_source]

    if not stat.S_ISDIR(source_mode):
        raise ValueError(f"source must be a regular file or directory: {source}")

    root = ensure_safe_input_directory(source)
    excluded = tuple(
        Path(os.path.abspath(path.expanduser()))
        for path in exclude_roots
        if path_is_beneath(path, root)
    )
    sources: list[Path] = []
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        directory_names[:] = [
            name
            for name in directory_names
            if not any(path_is_beneath(current / name, path) for path in excluded)
        ]
        visible_files = [
            name
            for name in file_names
            if not any(path_is_beneath(current / name, path) for path in excluded)
        ]
        for name in (*directory_names, *visible_files):
            candidate = current / name
            try:
                mode = candidate.lstat().st_mode
            except FileNotFoundError as exc:
                raise ValueError(f"source tree changed while scanning: {candidate}") from exc
            if stat.S_ISLNK(mode):
                raise ValueError(f"source tree contains symlink: {candidate}")
        for name in visible_files:
            candidate = current / name
            if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            sources.append(ensure_safe_input_file(candidate))
    return sorted(sources)


def output_directory_identity(path: Path) -> tuple[int, int]:
    mode = path.lstat()
    if stat.S_ISLNK(mode.st_mode) or not stat.S_ISDIR(mode.st_mode):
        raise ValueError(f"output_dir is not a regular directory: {path}")
    return mode.st_dev, mode.st_ino


def revalidate_output_directory(
    path: Path,
    expected_identity: tuple[int, int],
) -> None:
    try:
        current_identity = output_directory_identity(path)
    except (FileNotFoundError, ValueError):
        raise ValueError(f"output_dir changed during processing: {path}") from None
    if current_identity != expected_identity:
        raise ValueError(f"output_dir changed during processing: {path}")


def convert_legacy_ppt(path: Path, converted_dir: Path, soffice: str | None) -> Path:
    try:
        from .convert_ppt_to_pptx import convert_one, find_soffice
    except ImportError:
        from convert_ppt_to_pptx import convert_one, find_soffice

    return convert_one(path, converted_dir, find_soffice(soffice))


def legacy_ppt_fallback(path: Path, conversion_error: Exception) -> ExtractionResult:
    try:
        fallback: LegacyPptTextResult = extract_legacy_ppt_text(path)
    except Exception as fallback_error:
        raise RuntimeError(
            "LibreOffice conversion failed or was unavailable, and OLE/CFB text-record fallback also failed. "
            f"Conversion error: {conversion_error}; fallback error: {fallback_error}"
        ) from fallback_error
    notes = [
        f"LibreOffice conversion failed or was unavailable: {conversion_error}",
        "Used OLE/CFB text-record fallback; treat this as partial text evidence, not full slide coverage.",
    ]
    if fallback.text_record_count == 0:
        notes.append("No legacy PPT text records were found; the file may be image-only or OCR-limited.")
    return ExtractionResult(
        actual_source=path,
        text=fallback.markdown,
        backend="legacy-ppt-ole-cfb-fallback",
        partial=True,
        notes=notes,
        text_record_count=fallback.text_record_count,
    )


def extract_source(path: Path, config: PipelineConfig, converted_dir: Path) -> ExtractionResult:
    suffix = path.suffix.lower()
    actual = path
    if suffix == ".ppt":
        try:
            actual = convert_legacy_ppt(path, converted_dir, config.soffice)
        except Exception as exc:
            return legacy_ppt_fallback(path, exc)
        suffix = ".pptx"

    if suffix == ".pptx":
        pptx_result = extract_pptx_result(actual)
        notes = []
        if pptx_result.partial:
            notes.append("ZIP/XML fallback omits some presentation relationships and speaker-note/media semantics.")
        if pptx_result.partial or pptx_result.blank_slides or pptx_result.media_objects:
            notes.append("Use OCR or manual slide inspection for blank-text slides and media before claiming complete coverage.")
        return ExtractionResult(
            actual_source=actual,
            text=pptx_result.markdown,
            backend=f"pptx:{pptx_result.backend}",
            partial=pptx_result.partial,
            notes=notes,
            slide_count=pptx_result.slide_count,
            blank_slides=pptx_result.blank_slides,
            media_objects=pptx_result.media_objects,
        )
    if suffix == ".pdf":
        pdf_result = extract_pdf_result(actual)
        notes = [
            f"PDF pages: {pdf_result.page_count}; empty text pages: {pdf_result.empty_pages}; text characters: {pdf_result.char_count}.",
        ]
        if pdf_result.low_coverage:
            notes.append(LOW_COVERAGE_WARNING)
        return ExtractionResult(
            actual_source=actual,
            text=pdf_result.markdown,
            backend=f"pdf:{pdf_result.backend}",
            notes=notes,
            low_coverage=pdf_result.low_coverage,
            empty_pages=pdf_result.empty_pages,
            char_count=pdf_result.char_count,
            page_count=pdf_result.page_count,
        )
    raise ValueError(f"unsupported source type: {path}")


def safe_stem(path: Path) -> str:
    stem = unicodedata.normalize("NFC", path.stem)
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in stem)


def output_stem_key(value: str) -> str:
    """Return a key safe for Unicode-normalizing, case-insensitive filesystems."""

    return unicodedata.normalize("NFC", value).casefold()


def disambiguated_stem(path: Path, source_identity: Path, used: dict[str, str]) -> str:
    """Return a stable output stem without overwriting same-named sources.

    A basename is kept for the common single-source case. When two sources
    share that basename, later outputs receive a short hash of their source
    path, so same-named files cannot overwrite one another.
    """

    base = safe_stem(path)
    base_key = output_stem_key(base)
    identity = source_identity.as_posix()
    if base_key not in used:
        used[base_key] = identity
        return base

    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    candidate = f"{base}-{digest}"
    suffix = 2
    while output_stem_key(candidate) in used:
        candidate = f"{base}-{digest}-{suffix}"
        suffix += 1
    used[output_stem_key(candidate)] = identity
    return candidate


def write_manifest(config: PipelineConfig, processed: list[ProcessedSource]) -> None:
    manifest = config.output_dir / "pipeline_manifest.md"
    lines = [
        "# PPT/PDF To Obsidian Pipeline Manifest",
        "",
        f"- Source: `{config.source}`",
        f"- Output directory: `{config.output_dir}`",
        f"- Mode: `{config.mode}`",
        f"- Course name: `{config.course_name}`",
        "",
        "## Processed Sources",
    ]
    for item in processed:
        lines.append(f"- `{item.source}` -> `{item.raw.relative_to(config.output_dir)}` -> `{item.cleaned.relative_to(config.output_dir)}`")
        lines.append(f"  - Extraction backend: `{item.backend}`")
        if item.text_record_count is not None:
            lines.append(f"  - Text records: {item.text_record_count}")
        if item.page_count is not None:
            lines.append(
                f"  - PDF pages: {item.page_count}; empty text pages: {item.empty_pages}; text characters: {item.char_count}"
            )
        if item.slide_count is not None:
            lines.append(
                f"  - PPTX slides: {item.slide_count}; slides without visible text: {item.blank_slides}; "
                f"media objects: {item.media_objects}"
            )
        if item.low_coverage:
            lines.append("  - Coverage: low text coverage; do not claim complete source coverage without OCR/manual inspection.")
        if item.partial:
            lines.append("  - Coverage: partial/fallback extraction; do not claim complete source coverage from this artifact alone.")
        for note in item.notes or []:
            lines.append(f"  - Note: {note}")

    lines += [
        "",
        "## Suggested Obsidian Structure",
        "",
        f"- `{config.overview_name}`",
        "- `01_<topic>.md`",
        "- `知识点详细版_含公式.md`",
        "- `知识点精简复习版_含公式.md`",
        "",
        "Output stems keep the source basename when unique and add a stable path hash on collisions; no source may overwrite another artifact.",
        "Use the cleaned extraction files as raw material. Rewrite them into primary notes; do not treat them as final notes.",
    ]
    write_text_no_follow(config.output_dir, manifest, "\n".join(lines) + "\n")


def write_placeholders(config: PipelineConfig) -> None:
    if not config.create_review_placeholders:
        return
    notes_dir = ensure_safe_directory(
        config.output_dir,
        config.output_dir / "notes_skeleton",
        create=True,
    )
    overview = ensure_safe_output_path(
        config.output_dir,
        notes_dir / safe_relative_config_path(config.overview_name, "overview_name"),
    )
    if not overview.exists():
        write_text_no_follow(
            config.output_dir,
            overview,
            f"# {config.course_name} 总览\n\n"
            "## 顺序导航\n\n"
            "待根据 cleaned extraction 添加章节笔记。\n\n"
            "## 总复习\n\n"
            "- [[知识点精简复习版_含公式|知识点精简复习版（含公式）]]\n"
            "- [[知识点详细版_含公式|知识点详细版（含公式）]]\n",
        )
    for name, title in [
        ("知识点详细版_含公式.md", "知识点详细版（含公式）"),
        ("知识点精简复习版_含公式.md", "知识点精简复习版（含公式）"),
    ]:
        path = ensure_safe_output_path(config.output_dir, notes_dir / name)
        if not path.exists():
            write_text_no_follow(
                config.output_dir,
                path,
                f"# {config.course_name}{title}\n\n待根据 cleaned extraction 重写。\n",
            )


def run(config: PipelineConfig) -> list[ProcessedSource]:
    converted_relative = safe_relative_config_path(config.converted_dir, "converted_dir")
    safe_relative_config_path(config.overview_name, "overview_name")
    output_root = ensure_nonsymlink_directory(
        config.output_dir.expanduser(),
        create=True,
    )
    output_identity = output_directory_identity(output_root)
    config.output_dir = output_root
    sources = iter_sources(config.source, exclude_roots=(output_root,))
    if not sources:
        raise SystemExit(f"no supported source files found in {config.source}")
    revalidate_output_directory(output_root, output_identity)

    raw_dir = ensure_safe_directory(
        config.output_dir,
        config.output_dir / "raw_extracted",
        create=True,
    )
    cleaned_dir = ensure_safe_directory(
        config.output_dir,
        config.output_dir / "cleaned",
        create=True,
    )
    converted_dir = ensure_safe_directory(
        config.output_dir,
        config.output_dir / converted_relative,
        create=False,
    )
    ensure_safe_directory(
        config.output_dir,
        config.output_dir / "notes_skeleton",
        create=False,
    )
    ensure_safe_output_path(
        config.output_dir,
        config.output_dir / "pipeline_manifest.md",
        create_parent=False,
    )

    processed: list[ProcessedSource] = []
    used_output_stems: dict[str, str] = {}
    for source in sources:
        if source.suffix.lower() == ".ppt":
            converted_dir = ensure_safe_directory(
                config.output_dir,
                converted_dir,
                create=True,
            )
        extraction = extract_source(source, config, converted_dir)
        output_stem = disambiguated_stem(extraction.actual_source, source, used_output_stems)
        raw_path = raw_dir / f"{output_stem}.md"
        cleaned_path = cleaned_dir / f"{output_stem}.md"
        write_text_no_follow(config.output_dir, raw_path, extraction.text)
        write_text_no_follow(
            config.output_dir,
            cleaned_path,
            clean_text(extraction.text, unicode_math=config.unicode_math),
        )
        processed.append(
            ProcessedSource(
                source=source,
                raw=raw_path,
                cleaned=cleaned_path,
                backend=extraction.backend,
                partial=extraction.partial,
                notes=extraction.notes,
                text_record_count=extraction.text_record_count,
                low_coverage=extraction.low_coverage,
                empty_pages=extraction.empty_pages,
                char_count=extraction.char_count,
                page_count=extraction.page_count,
                slide_count=extraction.slide_count,
                blank_slides=extraction.blank_slides,
                media_objects=extraction.media_objects,
            )
        )

    write_manifest(config, processed)
    write_placeholders(config)
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract and clean PPT/PDF sources for Obsidian note rewriting.")
    parser.add_argument("source", nargs="?", help="Source file or directory")
    parser.add_argument("--config", type=Path, help="YAML config path")
    parser.add_argument("--output-dir", help="Output directory")
    parser.add_argument("--mode", choices=["course-notes", "research-presentation", "exam-review"])
    parser.add_argument("--unicode-math", action="store_true")
    parser.add_argument("--soffice", help="Path to LibreOffice soffice binary")
    args = parser.parse_args()

    try:
        config = config_from_args(args)
        processed = run(config)
    except PipelineConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"processed_sources {len(processed)}")
    print(config.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
