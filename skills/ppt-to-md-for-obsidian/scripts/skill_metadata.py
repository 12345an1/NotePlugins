#!/usr/bin/env python3
"""Structured validation for SKILL.md and agents/openai.yaml metadata."""

from __future__ import annotations

import ast
import re
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---(?:\n|$)", re.S)
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_FRONTMATTER_KEYS = frozenset({"name", "description"})
ALLOWED_TOP_LEVEL_KEYS = frozenset({"interface", "dependencies", "policy"})
ALLOWED_INTERFACE_KEYS = frozenset(
    {"display_name", "short_description", "icon_small", "icon_large", "brand_color", "default_prompt"}
)
ALLOWED_DEPENDENCIES_KEYS = frozenset({"tools"})
ALLOWED_TOOL_KEYS = frozenset({"type", "value", "description", "transport", "url"})
STRING_INTERFACE_KEYS = ALLOWED_INTERFACE_KEYS


class MetadataValidationError(ValueError):
    """Raised when skill metadata violates the supported schema."""


def _fallback_scalar(value: str) -> Any:
    lowered = value.casefold()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    if value.startswith(("'", '"')):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise MetadataValidationError(f"invalid quoted YAML scalar: {value}") from exc
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)", value):
        return float(value)
    return value


def _fallback_yaml_mapping(
    text: str,
    *,
    require_quoted_strings: bool = False,
) -> dict[str, Any]:
    """Parse the mapping subset used by bundled metadata when PyYAML is absent."""

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise MetadataValidationError(f"line {line_number}: tabs are not allowed for YAML indentation")
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if stripped.startswith("-"):
            raise MetadataValidationError("PyYAML is required to validate sequence-valued metadata")
        if ":" not in stripped:
            raise MetadataValidationError(f"line {line_number}: expected a YAML mapping entry")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise MetadataValidationError(f"line {line_number}: YAML mapping key is empty")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise MetadataValidationError(f"line {line_number}: invalid YAML indentation")
        parent = stack[-1][1]
        if key in parent:
            raise MetadataValidationError(f"line {line_number}: duplicate YAML key {key!r}")
        if raw_value.strip():
            raw_scalar = raw_value.strip()
            value = _fallback_scalar(raw_scalar)
            if require_quoted_strings and isinstance(value, str) and not raw_scalar.startswith(("'", '"')):
                raise MetadataValidationError(
                    f"line {line_number}: string value for {key!r} must be quoted"
                )
            parent[key] = value
        else:
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
    return root


def _pyyaml_load_mapping(
    text: str,
    label: str,
    *,
    require_quoted_strings: bool,
) -> dict[str, Any]:
    import yaml

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_unique_mapping(loader: UniqueKeyLoader, node: Any, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                line_number = key_node.start_mark.line + 1
                raise MetadataValidationError(f"{label} line {line_number}: duplicate YAML key {key!r}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_unique_mapping,
    )

    try:
        data = yaml.load(text, Loader=UniqueKeyLoader)
        if require_quoted_strings:
            root_node = yaml.compose(text, Loader=yaml.SafeLoader)
            seen_nodes: set[int] = set()

            def check_string_values(node: Any, *, is_value: bool = False) -> None:
                if id(node) in seen_nodes:
                    return
                seen_nodes.add(id(node))
                if isinstance(node, yaml.nodes.MappingNode):
                    for _key_node, value_node in node.value:
                        check_string_values(value_node, is_value=True)
                elif isinstance(node, yaml.nodes.SequenceNode):
                    for value_node in node.value:
                        check_string_values(value_node, is_value=True)
                elif (
                    is_value
                    and isinstance(node, yaml.nodes.ScalarNode)
                    and node.tag == "tag:yaml.org,2002:str"
                    and node.style not in {"'", '"'}
                ):
                    line_number = node.start_mark.line + 1
                    raise MetadataValidationError(
                        f"{label} line {line_number}: string value {node.value!r} must be quoted"
                    )

            if root_node is not None:
                check_string_values(root_node)
    except yaml.YAMLError as exc:
        raise MetadataValidationError(f"{label} contains invalid YAML: {exc}") from exc
    return data


def load_yaml_mapping(
    text: str,
    label: str,
    *,
    require_quoted_strings: bool = False,
) -> dict[str, Any]:
    try:
        import yaml  # noqa: F401
    except ImportError:
        data = _fallback_yaml_mapping(text, require_quoted_strings=require_quoted_strings)
    else:
        data = _pyyaml_load_mapping(
            text,
            label,
            require_quoted_strings=require_quoted_strings,
        )
    if not isinstance(data, dict):
        raise MetadataValidationError(f"{label} must be a YAML mapping")
    if not all(isinstance(key, str) for key in data):
        raise MetadataValidationError(f"{label} keys must be strings")
    return data


def load_skill_frontmatter(path: Path, expected_name: str | None = None) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise MetadataValidationError(f"{path}: SKILL.md must start with YAML frontmatter")
    data = load_yaml_mapping(match.group(1), f"{path} frontmatter")
    unexpected = sorted(set(data) - ALLOWED_FRONTMATTER_KEYS)
    if unexpected:
        raise MetadataValidationError(f"{path}: unexpected frontmatter keys: {', '.join(unexpected)}")
    for key in ("name", "description"):
        if key not in data:
            raise MetadataValidationError(f"{path}: frontmatter missing {key}")
        if not isinstance(data[key], str) or not data[key].strip():
            raise MetadataValidationError(f"{path}: frontmatter {key} must be a non-empty string")
    name = data["name"].strip()
    description = data["description"].strip()
    if not SKILL_NAME_RE.fullmatch(name):
        raise MetadataValidationError(f"{path}: frontmatter name must use lowercase letters, digits, and single hyphens")
    if len(name) > 64:
        raise MetadataValidationError(f"{path}: frontmatter name must be at most 64 characters")
    if len(description) > 1024 or "<" in description or ">" in description:
        raise MetadataValidationError(f"{path}: frontmatter description must be at most 1024 characters and contain no angle brackets")
    if expected_name is not None and name != expected_name:
        raise MetadataValidationError(f"{path}: frontmatter name {name!r} does not match directory {expected_name!r}")
    return data


def _require_mapping(parent: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise MetadataValidationError(f"{label}.{key} must be a mapping")
    return value


def _validate_icon_asset(metadata_path: Path, key: str, value: str) -> None:
    relative = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value.strip()
        or "\\" in value
        or relative.is_absolute()
        or windows.drive
        or windows.anchor
        or ".." in relative.parts
        or ".." in windows.parts
    ):
        raise MetadataValidationError(
            f"{metadata_path}: interface.{key} must be a relative path inside the skill directory"
        )

    skill_dir = metadata_path.parent.parent
    candidate = skill_dir.joinpath(*relative.parts)
    current = skill_dir
    for index, component in enumerate(relative.parts):
        current = current / component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            raise MetadataValidationError(
                f"{metadata_path}: interface.{key} asset does not exist: {value}"
            ) from None
        if stat.S_ISLNK(mode):
            raise MetadataValidationError(
                f"{metadata_path}: interface.{key} asset path must not contain symlinks: {value}"
            )
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode):
            raise MetadataValidationError(
                f"{metadata_path}: interface.{key} asset parent is not a directory: {value}"
            )
    if not candidate.is_file() or not stat.S_ISREG(candidate.lstat().st_mode):
        raise MetadataValidationError(
            f"{metadata_path}: interface.{key} must reference a regular file: {value}"
        )


def validate_openai_yaml(path: Path, skill_name: str) -> dict[str, Any]:
    data = load_yaml_mapping(
        path.read_text(encoding="utf-8"),
        str(path),
        require_quoted_strings=True,
    )
    unexpected = sorted(set(data) - ALLOWED_TOP_LEVEL_KEYS)
    if unexpected:
        raise MetadataValidationError(f"{path}: unexpected top-level keys: {', '.join(unexpected)}")
    interface = _require_mapping(data, "interface", str(path))
    unexpected_interface = sorted(set(interface) - ALLOWED_INTERFACE_KEYS)
    if unexpected_interface:
        raise MetadataValidationError(f"{path}: unexpected interface keys: {', '.join(unexpected_interface)}")
    for key in ("display_name", "short_description", "default_prompt"):
        value = interface.get(key)
        if not isinstance(value, str) or not value.strip():
            raise MetadataValidationError(f"{path}: interface.{key} must be a non-empty string")
    for key in STRING_INTERFACE_KEYS & interface.keys():
        if not isinstance(interface[key], str):
            raise MetadataValidationError(f"{path}: interface.{key} must be a string")
    for key in ("icon_small", "icon_large"):
        if key in interface:
            _validate_icon_asset(path, key, interface[key])
    if "brand_color" in interface and not re.fullmatch(r"#[0-9A-Fa-f]{6}", interface["brand_color"]):
        raise MetadataValidationError(f"{path}: interface.brand_color must use #RRGGBB hex format")
    short_description = interface["short_description"]
    if not 25 <= len(short_description) <= 64:
        raise MetadataValidationError(f"{path}: interface.short_description must be 25-64 characters")
    token_pattern = re.compile(rf"(?<![A-Za-z0-9_$-])\${re.escape(skill_name)}(?![A-Za-z0-9_-])")
    if not token_pattern.search(interface["default_prompt"]):
        raise MetadataValidationError(f"{path}: interface.default_prompt must mention exact token ${skill_name}")
    if "policy" in data:
        policy = _require_mapping(data, "policy", str(path))
        unexpected_policy = sorted(set(policy) - {"allow_implicit_invocation"})
        if unexpected_policy:
            raise MetadataValidationError(f"{path}: unexpected policy keys: {', '.join(unexpected_policy)}")
        if "allow_implicit_invocation" in policy and not isinstance(policy["allow_implicit_invocation"], bool):
            raise MetadataValidationError(f"{path}: policy.allow_implicit_invocation must be a boolean")
    if "dependencies" in data:
        dependencies = _require_mapping(data, "dependencies", str(path))
        unexpected_dependencies = sorted(set(dependencies) - ALLOWED_DEPENDENCIES_KEYS)
        if unexpected_dependencies:
            raise MetadataValidationError(
                f"{path}: unexpected dependencies keys: {', '.join(unexpected_dependencies)}"
            )
        tools = dependencies.get("tools")
        if not isinstance(tools, list):
            raise MetadataValidationError(f"{path}: dependencies.tools must be a list")
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict):
                raise MetadataValidationError(f"{path}: dependencies.tools[{index}] must be a mapping")
            unexpected_tool = sorted(set(tool) - ALLOWED_TOOL_KEYS)
            if unexpected_tool:
                raise MetadataValidationError(
                    f"{path}: unexpected dependencies.tools[{index}] keys: "
                    + ", ".join(unexpected_tool)
                )
            if tool.get("type") != "mcp":
                raise MetadataValidationError(f"{path}: dependencies.tools[{index}].type must be 'mcp'")
            value = tool.get("value")
            if not isinstance(value, str) or not value.strip():
                raise MetadataValidationError(
                    f"{path}: dependencies.tools[{index}].value must be a non-empty string"
                )
            for key in ("description", "transport", "url"):
                if key in tool and (
                    not isinstance(tool[key], str) or not tool[key].strip()
                ):
                    raise MetadataValidationError(
                        f"{path}: dependencies.tools[{index}].{key} must be a non-empty string"
                    )
    return data


def validate_skill_metadata(skill_dir: Path) -> dict[str, Any]:
    frontmatter = load_skill_frontmatter(skill_dir / "SKILL.md", expected_name=skill_dir.name)
    validate_openai_yaml(skill_dir / "agents" / "openai.yaml", frontmatter["name"])
    return frontmatter
