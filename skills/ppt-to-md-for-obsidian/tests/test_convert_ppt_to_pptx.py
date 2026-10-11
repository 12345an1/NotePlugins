from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from scripts import convert_ppt_to_pptx
from scripts.convert_ppt_to_pptx import convert_one, find_soffice, iter_inputs, soffice_candidates
import scripts.safe_io as safe_io


def write_minimal_pptx(path: Path, marker: str = "fresh") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", f"<presentation>{marker}</presentation>")


def staging_directory(command: list[str]) -> Path:
    return Path(command[command.index("--outdir") + 1])


def completed_run(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    return SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
    )


def test_soffice_candidates_include_cross_platform_names():
    candidates = soffice_candidates("/custom/soffice")

    assert candidates[0] == "/custom/soffice"
    assert "soffice" in candidates
    assert "soffice.exe" in candidates
    assert "libreoffice" in candidates
    assert "libreoffice.exe" in candidates
    assert "/Applications/LibreOffice.app/Contents/MacOS/soffice" in candidates
    assert r"C:\Program Files\LibreOffice\program\soffice.exe" in candidates
    assert r"C:\Program Files (x86)\LibreOffice\program\soffice.exe" in candidates


def test_find_soffice_prefers_explicit_existing_path(tmp_path: Path):
    executable = tmp_path / "soffice"
    executable.write_text("# fake soffice\n", encoding="utf-8")

    assert find_soffice(str(executable)) == str(executable)


def test_find_soffice_uses_path_lookup(monkeypatch: pytest.MonkeyPatch):
    def fake_which(candidate: str) -> str | None:
        if candidate == "soffice.exe":
            return r"C:\LibreOffice\program\soffice.exe"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    assert find_soffice() == r"C:\LibreOffice\program\soffice.exe"


def test_iter_inputs_finds_case_variant_legacy_ppt_extensions(tmp_path: Path) -> None:
    (tmp_path / "lower.ppt").write_bytes(b"legacy")
    (tmp_path / "UPPER.PPT").write_bytes(b"legacy")
    (tmp_path / "MiXeD.PpT").write_bytes(b"legacy")
    (tmp_path / "already.pptx").write_bytes(b"modern")
    (tmp_path / "directory.ppt").mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"external legacy")
    (tmp_path / "linked.ppt").symlink_to(outside)

    assert {path.name for path in iter_inputs(tmp_path)} == {
        "lower.ppt",
        "UPPER.PPT",
        "MiXeD.PpT",
    }


def test_convert_one_rejects_explicit_source_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.ppt"
    source.write_bytes(b"legacy")
    alias = tmp_path / "alias.ppt"
    alias.symlink_to(source)

    with pytest.raises(ValueError, match="input path is a symlink"):
        convert_one(alias, tmp_path / "out", "soffice")


def test_convert_one_rejects_successful_noop_with_stale_expected_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "lecture.ppt"
    source.write_bytes(b"legacy")
    out_dir = tmp_path / "converted"
    out_dir.mkdir()
    expected = out_dir / "lecture.pptx"
    expected.write_bytes(b"stale")
    monkeypatch.setattr(
        convert_ppt_to_pptx,
        "run_capture",
        lambda *args, **kwargs: completed_run(),
    )

    with pytest.raises(RuntimeError, match="without producing"):
        convert_one(source, out_dir, "soffice")

    assert expected.read_bytes() == b"stale"


def test_convert_one_rejects_expected_output_symlink_before_running_converter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "lecture.ppt"
    source.write_bytes(b"legacy")
    out_dir = tmp_path / "converted"
    out_dir.mkdir()
    sentinel = tmp_path / "sentinel.pptx"
    sentinel.write_bytes(b"sentinel")
    (out_dir / "lecture.pptx").symlink_to(sentinel)
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return completed_run()

    monkeypatch.setattr(convert_ppt_to_pptx, "run_capture", fake_run)

    with pytest.raises(ValueError, match="symlink"):
        convert_one(source, out_dir, "soffice")

    assert called is False
    assert sentinel.read_bytes() == b"sentinel"


def test_convert_one_ignores_unrelated_output_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "lecture.ppt"
    source.write_bytes(b"legacy")
    out_dir = tmp_path / "converted"
    out_dir.mkdir()
    sentinel = tmp_path / "sentinel.pptx"
    sentinel.write_bytes(b"sentinel")
    (out_dir / "other.pptx").symlink_to(sentinel)

    def fake_run(command: list[str], *_args, **_kwargs):
        write_minimal_pptx(staging_directory(command) / "lecture.pptx")
        return completed_run()

    monkeypatch.setattr(convert_ppt_to_pptx, "run_capture", fake_run)

    converted = convert_one(source, out_dir, "soffice")

    assert converted == out_dir / "lecture.pptx"
    assert sentinel.read_bytes() == b"sentinel"


def test_convert_one_atomically_replaces_hardlink_without_mutating_external_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "lecture.ppt"
    source.write_bytes(b"legacy")
    out_dir = tmp_path / "converted"
    out_dir.mkdir()
    sentinel = tmp_path / "sentinel.pptx"
    sentinel.write_bytes(b"sentinel")
    expected = out_dir / "lecture.pptx"
    os.link(sentinel, expected)

    def fake_run(command: list[str], *_args, **_kwargs):
        write_minimal_pptx(staging_directory(command) / "lecture.pptx")
        return completed_run()

    monkeypatch.setattr(convert_ppt_to_pptx, "run_capture", fake_run)

    converted = convert_one(source, out_dir, "soffice")

    assert converted == expected
    assert sentinel.read_bytes() == b"sentinel"
    assert expected.stat().st_ino != sentinel.stat().st_ino
    with ZipFile(expected) as archive:
        assert "ppt/presentation.xml" in archive.namelist()


def test_convert_one_rejects_invalid_staged_package_and_preserves_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "lecture.ppt"
    source.write_bytes(b"legacy")
    out_dir = tmp_path / "converted"
    out_dir.mkdir()
    expected = out_dir / "lecture.pptx"
    expected.write_bytes(b"original")

    def fake_run(command: list[str], *_args, **_kwargs):
        (staging_directory(command) / "lecture.pptx").write_bytes(b"not a PPTX")
        return completed_run()

    monkeypatch.setattr(convert_ppt_to_pptx, "run_capture", fake_run)

    with pytest.raises(RuntimeError, match="invalid PPTX package"):
        convert_one(source, out_dir, "soffice")

    assert expected.read_bytes() == b"original"


def test_convert_one_does_not_return_unrelated_changed_pptx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "lecture.ppt"
    source.write_bytes(b"legacy")
    out_dir = tmp_path / "converted"
    out_dir.mkdir()
    unrelated = out_dir / "unrelated.pptx"
    unrelated.write_bytes(b"old")

    def fake_run(command: list[str], *_args, **_kwargs):
        unrelated.write_bytes(b"changed")
        write_minimal_pptx(staging_directory(command) / "unrelated.pptx")
        return completed_run()

    monkeypatch.setattr(convert_ppt_to_pptx, "run_capture", fake_run)

    with pytest.raises(RuntimeError, match="expected output lecture.pptx"):
        convert_one(source, out_dir, "soffice")

    assert unrelated.read_bytes() == b"changed"
    assert not (out_dir / "lecture.pptx").exists()


def test_convert_one_publish_failure_preserves_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not safe_io._supports_dir_fd():
        pytest.skip("directory-relative file operations are unavailable")
    source = tmp_path / "lecture.ppt"
    source.write_bytes(b"legacy")
    out_dir = tmp_path / "converted"
    out_dir.mkdir()
    expected = out_dir / "lecture.pptx"
    expected.write_bytes(b"original")

    def fake_run(command: list[str], *_args, **_kwargs):
        write_minimal_pptx(staging_directory(command) / "lecture.pptx")
        return completed_run()

    monkeypatch.setattr(convert_ppt_to_pptx, "run_capture", fake_run)
    monkeypatch.setattr(safe_io, "_directory_identity_matches", lambda parent_fd, parent: False)

    with pytest.raises(ValueError, match="parent directory changed"):
        convert_one(source, out_dir, "soffice")

    assert expected.read_bytes() == b"original"
    assert list(out_dir.glob(".lecture.pptx.*.tmp")) == []
