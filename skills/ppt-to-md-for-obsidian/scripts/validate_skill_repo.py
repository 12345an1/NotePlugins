#!/usr/bin/env python3
"""Validate the skill folder structure and lightweight metadata."""

from __future__ import annotations

from pathlib import Path
import re
import sys

try:
    from .skill_metadata import (
        MetadataValidationError,
        load_skill_frontmatter,
        validate_openai_yaml as validate_openai_metadata,
    )
except ImportError:
    from skill_metadata import (
        MetadataValidationError,
        load_skill_frontmatter,
        validate_openai_yaml as validate_openai_metadata,
    )


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = ['LICENSE',
 'agents/openai.yaml',
 'scripts/check_obsidian_links.py',
 'scripts/markdown_links.py',
 'scripts/check_course_notes.py',
 'scripts/check_source_coverage.py',
 'scripts/clean_latex_from_ppt.py',
 'scripts/convert_ppt_to_pptx.py',
 'scripts/extract_legacy_ppt_text.py',
 'scripts/extract_pdf_text.py',
 'scripts/extract_pptx_text.py',
 'scripts/ppt_to_obsidian_pipeline.py',
 'scripts/run_with_timeout.py',
 'scripts/safe_io.py',
 'scripts/skill_metadata.py',
 'scripts/validate_skill_repo.py',
 'references/modes.md',
 'references/obsidian-style.md',
 'references/validation.md',
 'references/solvenotes-profile.md']
SUCCESS_MESSAGE = "skill_repo_validation ok"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_yaml(text: str):
    try:
        import yaml
    except ImportError as exc:
        fail(f"PyYAML is required for YAML validation: {exc}")
    return yaml.safe_load(text)


def load_skill_metadata() -> dict:
    path = ROOT / "SKILL.md"
    if not path.exists():
        fail("SKILL.md is missing")
    try:
        return load_skill_frontmatter(path, expected_name=ROOT.name)
    except (OSError, MetadataValidationError) as exc:
        fail(str(exc))


def validate_openai_yaml(skill_name: str) -> None:
    path = ROOT / "agents" / "openai.yaml"
    if not path.exists():
        fail("agents/openai.yaml is missing")
    try:
        validate_openai_metadata(path, skill_name)
    except (OSError, MetadataValidationError) as exc:
        fail(str(exc))


def validate_yaml_files() -> None:
    for path in sorted(ROOT.rglob("*.yml")) + sorted(ROOT.rglob("*.yaml")):
        if ".git" in path.parts:
            continue
        try:
            load_yaml(path.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"invalid YAML in {path.relative_to(ROOT)}: {exc}")


def validate_references_exist() -> None:
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    for target in re.findall(r"`(references/[^`]+\.md|scripts/[^`]+\.py)`", text):
        if not (ROOT / target).exists():
            fail(f"referenced bundled resource is missing: {target}")


def validate_required_files() -> None:
    for target in REQUIRED_FILES:
        if not (ROOT / target).exists():
            fail(f"required bundled file is missing: {target}")


def main() -> int:
    metadata = load_skill_metadata()
    validate_openai_yaml(metadata["name"])
    validate_required_files()
    validate_yaml_files()
    validate_references_exist()
    print(SUCCESS_MESSAGE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
