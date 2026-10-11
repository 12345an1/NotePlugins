#!/usr/bin/env python3
"""Check and install local dependencies for pdf-ocr-to-markdown."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REQUIRED_MODULES = {
    "pdfplumber": "pdfplumber",
    "pypdf": "pypdf",
    "fitz": "pymupdf",
    "PIL": "Pillow",
}

def module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def check_dependencies() -> dict[str, Any]:
    modules = {
        module: {
            "package": package,
            "available": module_available(module),
        }
        for module, package in REQUIRED_MODULES.items()
    }
    missing_packages = sorted(
        {details["package"] for details in modules.values() if not details["available"]}
    )
    return {
        "ok": not missing_packages,
        "modules": modules,
        "missing_packages": missing_packages,
        "install_command": [sys.executable, "-m", "pip", "install", *missing_packages]
        if missing_packages
        else [],
    }


def install_dependencies(packages: list[str]) -> dict[str, Any]:
    if not packages:
        return {"installed": [], "returncode": 0, "stdout": "", "stderr": ""}

    command = [sys.executable, "-m", "pip", "install", *packages]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    return {
        "installed": packages if result.returncode == 0 else [],
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "command": command,
    }


def write_status(path: str | Path, status: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up local dependencies for pdf-ocr-to-markdown.")
    parser.add_argument("--check", action="store_true", help="Only check dependency status")
    parser.add_argument("--install", action="store_true", help="Install missing required Python packages")
    parser.add_argument("--status-json", default=None, help="Optional path to write setup status JSON")
    args = parser.parse_args()

    before = check_dependencies()
    result: dict[str, Any] = {"before": before, "install": None, "after": before}

    if args.install and before["missing_packages"]:
        install_result = install_dependencies(before["missing_packages"])
        after = check_dependencies()
        result = {"before": before, "install": install_result, "after": after}
        exit_code = 0 if after["ok"] else 1
    else:
        exit_code = 0 if before["ok"] else 1

    if args.status_json:
        write_status(args.status_json, result)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
