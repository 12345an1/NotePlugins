from __future__ import annotations

from pathlib import Path
import struct
import sys
from zipfile import ZipFile

import pytest

from scripts import clean_latex_from_ppt, extract_legacy_ppt_text, extract_pdf_text, extract_pptx_text
from scripts.extract_legacy_ppt_text import LegacyPptTextResult


@pytest.fixture
def cli_case(request, monkeypatch, tmp_path: Path) -> tuple[object, list[str]]:
    input_path = tmp_path / "input.md"
    input_path.write_text("# Input\n", encoding="utf-8")
    if request.param == "clean":
        return clean_latex_from_ppt, [str(input_path)]
    if request.param == "pptx":
        source = tmp_path / "input.pptx"
        with ZipFile(source, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("ppt/presentation.xml", "<presentation/>")
        monkeypatch.setattr(extract_pptx_text, "extract_pptx", lambda *args, **kwargs: "# PPTX\n")
        return extract_pptx_text, [str(source)]
    if request.param == "pdf":
        source = tmp_path / "input.pdf"
        source.write_bytes(b"%PDF-1.4\n")
        monkeypatch.setattr(extract_pdf_text, "extract_pdf", lambda path: "# PDF\n")
        return extract_pdf_text, [str(source)]
    source = tmp_path / "input.ppt"
    source.write_bytes(b"placeholder")
    monkeypatch.setattr(
        extract_legacy_ppt_text,
        "extract_legacy_ppt_text",
        lambda path: LegacyPptTextResult(source=path, text="Legacy text", text_record_count=1),
    )
    return extract_legacy_ppt_text, [str(source)]


@pytest.mark.parametrize("cli_case", ["clean", "pptx", "pdf", "legacy"], indirect=True)
@pytest.mark.parametrize("kind", ["final", "parent", "ancestor"])
def test_output_clis_reject_symlink_components(
    cli_case: tuple[object, list[str]],
    kind: str,
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    module, arguments = cli_case
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.md"
    sentinel.write_text("keep\n", encoding="utf-8")
    if kind == "final":
        output = tmp_path / "result.md"
        output.symlink_to(sentinel)
    elif kind == "parent":
        parent = tmp_path / "linked-parent"
        parent.symlink_to(outside, target_is_directory=True)
        output = parent / "result.md"
    else:
        ancestor = tmp_path / "linked-ancestor"
        ancestor.symlink_to(outside, target_is_directory=True)
        output = ancestor / "nested" / "result.md"
    monkeypatch.setattr(sys, "argv", [module.__file__, *arguments, "--out", str(output)])

    result = module.main()

    captured = capsys.readouterr()
    assert result == 1
    assert "symlink" in captured.err.lower()
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert not (outside / "result.md").exists()
    assert not (outside / "nested" / "result.md").exists()


@pytest.mark.parametrize(
    "module, suffix",
    [
        (clean_latex_from_ppt, ".md"),
        (extract_pdf_text, ".pdf"),
        (extract_legacy_ppt_text, ".ppt"),
    ],
)
@pytest.mark.parametrize("kind", ["leaf", "ancestor", "broken"])
def test_input_clis_reject_symlink_components(
    module: object,
    suffix: str,
    kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "real" / f"input{suffix}"
    target.parent.mkdir()
    target.write_bytes(b"placeholder")
    if kind == "leaf":
        source = tmp_path / f"input-link{suffix}"
        source.symlink_to(target)
    elif kind == "ancestor":
        alias = tmp_path / "real-link"
        alias.symlink_to(target.parent, target_is_directory=True)
        source = alias / target.name
    else:
        source = tmp_path / f"broken{suffix}"
        source.symlink_to(tmp_path / f"missing{suffix}")

    monkeypatch.setattr(sys, "argv", [module.__file__, str(source)])

    assert module.main() == 1
    captured = capsys.readouterr()
    assert "symlink" in captured.err.lower() or "does not exist" in captured.err.lower()
    assert "Traceback" not in captured.err


def test_legacy_parser_rejects_excessive_record_nesting() -> None:
    payload = b""
    for _ in range(extract_legacy_ppt_text.MAX_PPT_RECORD_NESTING + 2):
        payload = struct.pack("<HHI", 0xF, 1, len(payload)) + payload

    with pytest.raises(ValueError, match="record nesting exceeds safety limit"):
        extract_legacy_ppt_text.parse_ppt_records(payload, [])


def test_legacy_cfb_rejects_truncated_header(tmp_path: Path) -> None:
    source = tmp_path / "truncated.ppt"
    source.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 32)

    with pytest.raises(ValueError, match="truncated Compound File Binary header"):
        extract_legacy_ppt_text.read_cfb_stream(source, "PowerPoint Document")


def test_version4_cfb_sector_zero_starts_after_the_4096_byte_header_sector() -> None:
    assert extract_legacy_ppt_text.cfb_sector_offset(0, 4096) == 4096


def test_legacy_cfb_rejects_difat_start_when_count_is_zero(tmp_path: Path) -> None:
    payload = bytearray(1024)
    payload[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<H", payload, 30, 9)
    struct.pack_into("<H", payload, 32, 6)
    struct.pack_into("<i", payload, 48, extract_legacy_ppt_text.END_OF_CHAIN)
    struct.pack_into("<I", payload, 56, 4096)
    struct.pack_into("<i", payload, 60, extract_legacy_ppt_text.END_OF_CHAIN)
    struct.pack_into("<i", payload, 68, 0)
    source = tmp_path / "inconsistent-difat.ppt"
    source.write_bytes(payload)

    with pytest.raises(ValueError, match="DIFAT start without DIFAT sectors"):
        extract_legacy_ppt_text.read_cfb_stream(source, "PowerPoint Document")
