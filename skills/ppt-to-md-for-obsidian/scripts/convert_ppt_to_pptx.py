#!/usr/bin/env python3
"""Convert legacy .ppt files to .pptx with LibreOffice.

The script is a thin wrapper around LibreOffice/soffice. It does not modify the
source file and writes converted files to an output directory.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import tempfile
from zipfile import BadZipFile, ZipFile

try:
    from .safe_io import (
        atomic_binary_writer,
        ensure_safe_directory,
        ensure_safe_input_directory,
        ensure_safe_input_file,
        ensure_safe_output_path,
    )
    from .run_with_timeout import run_capture
except ImportError:
    from safe_io import (
        atomic_binary_writer,
        ensure_safe_directory,
        ensure_safe_input_directory,
        ensure_safe_input_file,
        ensure_safe_output_path,
    )
    from run_with_timeout import run_capture


MACOS_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
WINDOWS_SOFFICE_PATHS = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)
REQUIRED_PPTX_PARTS = {"[Content_Types].xml", "ppt/presentation.xml"}


def soffice_candidates(explicit: str | None = None) -> list[str]:
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(["soffice", "soffice.exe", "libreoffice", "libreoffice.exe"])
    candidates.append(MACOS_SOFFICE)
    candidates.extend(WINDOWS_SOFFICE_PATHS)
    return candidates


def find_soffice(explicit: str | None = None) -> str:
    for candidate in soffice_candidates(explicit):
        direct = Path(candidate).expanduser()
        if direct.exists():
            return str(direct)
        found = shutil.which(candidate)
        if found:
            return found

    raise SystemExit(
        "LibreOffice was not found. Install LibreOffice or pass --soffice "
        "with the path to the soffice executable. On Windows this is often "
        r"C:\Program Files\LibreOffice\program\soffice.exe."
    )


def validate_pptx_package(path: Path) -> None:
    """Reject converter output that is not an intact PPTX package."""

    try:
        with ZipFile(path) as archive:
            corrupt = archive.testzip()
            if corrupt is not None:
                raise RuntimeError(f"LibreOffice produced a PPTX with a corrupt ZIP member: {corrupt}")
            missing = sorted(REQUIRED_PPTX_PARTS - set(archive.namelist()))
            if missing:
                raise RuntimeError(
                    "LibreOffice produced a PPTX missing required package parts: "
                    + ", ".join(missing)
                )
    except (BadZipFile, OSError) as exc:
        raise RuntimeError(f"LibreOffice produced an invalid PPTX package: {path}: {exc}") from exc


def convert_one(path: Path, out_dir: Path, soffice: str) -> Path:
    path = ensure_safe_input_file(path)
    if path.suffix.lower() != ".ppt":
        raise ValueError(f"expected a .ppt file, got: {path}")

    out_dir = ensure_safe_directory(out_dir, create=True)
    expected = ensure_safe_output_path(out_dir / f"{path.stem}.pptx", create_parent=False)
    with tempfile.TemporaryDirectory(prefix=".ppt-convert-", dir=out_dir) as staging_name:
        staging_dir = Path(staging_name)
        staged = staging_dir / expected.name
        cmd = [
            soffice,
            "--headless",
            "--convert-to",
            "pptx",
            "--outdir",
            str(staging_dir),
            str(path),
        ]
        result = run_capture(cmd, 180, f"LibreOffice conversion: {path.name}")
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        if result.timed_out:
            raise RuntimeError(
                "LibreOffice conversion timed out after 180 seconds\n"
                f"command: {' '.join(cmd)}\nstdout: {stdout}\nstderr: {stderr}"
            )
        if result.returncode != 0:
            raise RuntimeError(
                "LibreOffice conversion failed\n"
                f"command: {' '.join(cmd)}\n"
                f"stdout: {stdout}\n"
                f"stderr: {stderr}"
            )

        staged = ensure_safe_output_path(staged, create_parent=False)
        if not staged.exists():
            raise RuntimeError(
                f"LibreOffice finished without producing expected output {expected.name}. "
                f"stdout: {stdout}\nstderr: {stderr}"
            )
        validate_pptx_package(staged)
        with staged.open("rb") as converted, atomic_binary_writer(expected) as output:
            shutil.copyfileobj(converted, output)

    return ensure_safe_output_path(expected, create_parent=False)


def iter_inputs(input_path: Path) -> list[Path]:
    if input_path.is_dir():
        input_path = ensure_safe_input_directory(input_path)
        return sorted(
            ensure_safe_input_file(path)
            for path in input_path.iterdir()
            if not path.is_symlink()
            and path.is_file()
            and path.suffix.casefold() == ".ppt"
        )
    return [input_path]


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert .ppt files to .pptx.")
    parser.add_argument("input", type=Path, help="A .ppt file or directory")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("converted_pptx"),
        help="Directory for converted .pptx files",
    )
    parser.add_argument("--soffice", help="Path to LibreOffice soffice binary")
    args = parser.parse_args()

    soffice = find_soffice(args.soffice)
    inputs = iter_inputs(args.input)
    if not inputs:
        parser.error(f"no .ppt files found in {args.input}")

    for path in inputs:
        converted = convert_one(path, args.out_dir, soffice)
        print(converted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
