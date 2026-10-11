from pathlib import Path
import struct
import subprocess
import sys
from zipfile import ZipFile

import pytest
import scripts.extract_pptx_text as extractor

from scripts.extract_pptx_text import (
    PptxExtractionError,
    extract_pptx,
    extract_pptx_result,
    extract_pptx_with_zip_result,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract_pptx_text.py"
PIPELINE_SCRIPT = ROOT / "scripts" / "ppt_to_obsidian_pipeline.py"
SUBPROCESS_TIMEOUT_SECONDS = 60


def write_required_package(
    path: Path,
    *,
    content_types: bytes = b"<Types/>",
    presentation: bytes = b"<presentation/>",
) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("ppt/presentation.xml", presentation)


def set_encrypted_flags(path: Path) -> None:
    """Mark every member encrypted without needing an encryption writer."""

    payload = bytearray(path.read_bytes())
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        offset = 0
        while (offset := payload.find(signature, offset)) >= 0:
            flags = struct.unpack_from("<H", payload, offset + flag_offset)[0]
            struct.pack_into("<H", payload, offset + flag_offset, flags | 0x1)
            offset += len(signature)
    path.write_bytes(payload)


def run_extractor(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )


def test_extract_pptx_sample_contains_ordered_slide_text():
    sample = Path("examples/sample-course/raw/sample_course.pptx")
    output = extract_pptx(sample)

    assert "# Extracted PPTX Text: sample_course.pptx" in output
    assert "## Slide 1: 机器学习导论" in output
    assert "- 经验风险与泛化" in output
    assert "- 知识点精简复习版_含公式.md" in output
    assert "- Backend: `python-pptx`" in output
    assert "- Slides:" in output


def test_extract_pptx_sample_keeps_python_backend_without_fallback() -> None:
    sample = Path("examples/sample-course/raw/sample_course.pptx")

    result = extract_pptx_result(sample)

    assert result.backend == "python-pptx"
    assert result.partial is False


def test_normal_backend_warns_when_text_extraction_cannot_cover_visual_content() -> None:
    from scripts.extract_pptx_text import extraction_header

    header = extraction_header(
        Path("visual.pptx"),
        backend="python-pptx",
        partial=False,
        slide_count=2,
        blank_slides=1,
        media_objects=1,
    )

    assert "- Partial fallback: false" in header
    assert any("not complete visual/OCR coverage" in line for line in header)


@pytest.mark.parametrize("kind", ["leaf", "ancestor", "broken"])
def test_extract_pptx_cli_rejects_symlinked_input(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "real" / "lecture.pptx"
    target.parent.mkdir()
    write_required_package(target)
    if kind == "leaf":
        source = tmp_path / "lecture-link.pptx"
        source.symlink_to(target)
    elif kind == "ancestor":
        alias = tmp_path / "real-link"
        alias.symlink_to(target.parent, target_is_directory=True)
        source = alias / target.name
    else:
        source = tmp_path / "broken.pptx"
        source.symlink_to(tmp_path / "missing.pptx")

    result = run_extractor(source)

    assert result.returncode == 1
    assert "symlink" in result.stderr.lower() or "does not exist" in result.stderr.lower()
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ("missing", "file does not exist"),
        ("directory", "input is not a regular file"),
        ("non-zip", "invalid PPTX ZIP package"),
        ("truncated", "invalid PPTX ZIP package"),
        ("encrypted", "encrypted PPTX packages are not supported"),
        ("missing-ooxml", "PPTX package is missing required OOXML parts"),
        ("invalid-ooxml", "PPTX package contains malformed required XML"),
    ],
)
def test_extract_pptx_cli_reports_stable_errors_without_tracebacks(
    tmp_path: Path,
    fixture: str,
    reason: str,
) -> None:
    source = tmp_path / f"{fixture}.pptx"
    if fixture == "directory":
        source.mkdir()
    elif fixture == "non-zip":
        source.write_bytes(b"not-a-zip")
    elif fixture == "truncated":
        write_required_package(source)
        source.write_bytes(source.read_bytes()[:-10])
    elif fixture == "encrypted":
        write_required_package(source)
        set_encrypted_flags(source)
    elif fixture == "missing-ooxml":
        with ZipFile(source, "w") as archive:
            archive.writestr("ppt/presentation.xml", "<presentation/>")
    elif fixture == "invalid-ooxml":
        write_required_package(source, content_types=b"<Types>")

    result = run_extractor(source)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"ERROR: {source}: {reason}\n"
    assert "Traceback" not in result.stderr


def test_pptx_reader_enforces_input_and_uncompressed_zip_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "budget.pptx"
    write_required_package(source)

    monkeypatch.setattr(extractor, "MAX_PPTX_INPUT_BYTES", source.stat().st_size - 1)
    with pytest.raises(PptxExtractionError, match="input exceeds"):
        extractor.extract_pptx_result(source)

    monkeypatch.setattr(extractor, "MAX_PPTX_INPUT_BYTES", source.stat().st_size + 1)
    with ZipFile(source) as archive:
        total = sum(member.file_size for member in archive.infolist())
    monkeypatch.setattr(extractor, "MAX_PPTX_TOTAL_UNCOMPRESSED_BYTES", total - 1)
    with pytest.raises(PptxExtractionError, match="total uncompressed size"):
        extractor.extract_pptx_result(source)


def test_pptx_reader_rejects_excessive_member_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "members.pptx"
    write_required_package(source)
    monkeypatch.setattr(extractor, "MAX_PPTX_ZIP_MEMBERS", 1)

    with pytest.raises(PptxExtractionError, match="too many members"):
        extractor.extract_pptx_result(source)


def test_pptx_reader_rejects_duplicate_zip_members(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.pptx"
    write_required_package(source)
    with pytest.warns(UserWarning, match="Duplicate name"):
        with ZipFile(source, "a") as archive:
            archive.writestr("ppt/presentation.xml", "<presentation duplicate='1'/>")

    with pytest.raises(PptxExtractionError, match="duplicate members"):
        extractor.extract_pptx_result(source)


def test_pipeline_reuses_structured_pptx_error_boundary(tmp_path: Path) -> None:
    source = tmp_path / "broken.pptx"
    source.write_bytes(b"not-a-zip")

    result = subprocess.run(
        [
            sys.executable,
            str(PIPELINE_SCRIPT),
            str(source),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"ERROR: {source}: invalid PPTX ZIP package\n"
    assert "Traceback" not in result.stderr


def test_shared_pptx_entry_hides_internal_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pptx

    sample = Path("examples/sample-course/raw/sample_course.pptx")

    def fail_parse(_path: str):
        raise RuntimeError("SENSITIVE_INTERNAL_DETAIL")

    monkeypatch.setattr(pptx, "Presentation", fail_parse)

    with pytest.raises(PptxExtractionError) as caught:
        extract_pptx_result(sample)

    assert str(caught.value) == f"{sample}: PPTX package could not be parsed"
    assert "SENSITIVE_INTERNAL_DETAIL" not in str(caught.value)


@pytest.mark.parametrize(
    "signal",
    [KeyboardInterrupt(), SystemExit(7)],
    ids=("keyboard-interrupt", "system-exit"),
)
def test_shared_pptx_entry_does_not_hide_control_flow_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    signal: BaseException,
) -> None:
    import pptx

    sample = Path("examples/sample-course/raw/sample_course.pptx")

    def stop_parse(_path: str):
        raise signal

    monkeypatch.setattr(pptx, "Presentation", stop_parse)

    with pytest.raises(type(signal)):
        extract_pptx_result(sample)


def test_zip_fallback_reports_backend_partial_and_slide_media_stats(tmp_path: Path):
    source = tmp_path / "fallback.pptx"
    text_slide = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Visible text</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    media_only_slide = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        "<p:cSld><p:spTree><p:pic /></p:spTree></p:cSld></p:sld>"
    )
    with ZipFile(source, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("ppt/presentation.xml", "<presentation />")
        archive.writestr("ppt/slides/slide1.xml", text_slide)
        archive.writestr("ppt/slides/slide2.xml", media_only_slide)

    result = extract_pptx_with_zip_result(source)

    assert result.backend == "zip-xml-fallback"
    assert result.partial is True
    assert result.slide_count == 2
    assert result.blank_slides == 1
    assert result.media_objects == 1
    assert "- Partial fallback: true" in result.markdown
    assert "Use OCR or manual slide inspection" in result.markdown


@pytest.mark.parametrize("include_media_placeholders", [True, False])
def test_legacy_unknown_shapes_keep_text_groups_and_media(
    tmp_path: Path, include_media_placeholders: bool
) -> None:
    from io import BytesIO

    from PIL import Image
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(0, 0, Inches(2), Inches(1)).text = "Recognized heading"
    group = slide.shapes.add_group_shape()

    def add_unknown(shapes, text):
        shape = shapes.add_shape(
            MSO_AUTO_SHAPE_TYPE.RECTANGLE, 0, Inches(1), Inches(2), Inches(1)
        )
        shape.text = text
        # Geometry-free p:sp matches the shape-type failure in converted .ppt.
        geometry = shape.element.find("{*}spPr/{*}prstGeom")
        assert geometry is not None
        geometry.getparent().remove(geometry)
        with pytest.raises(NotImplementedError):
            _ = shape.shape_type
        return shape

    add_unknown(group.shapes, "Legacy grouped text")
    add_unknown(slide.shapes, "Legacy top-level text")
    add_unknown(slide.shapes, "")
    png = BytesIO()
    Image.new("RGB", (2, 2), "white").save(png, format="PNG")
    for shapes in (slide.shapes, group.shapes):
        shapes.add_picture(BytesIO(png.getvalue()), Inches(3), Inches(2), Inches(1), Inches(1))
    data = CategoryChartData()
    data.categories = ["A", "B"]
    data.add_series("Count", [1, 2])
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, 0, Inches(3), Inches(2), Inches(2), data
    )
    blank_slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_unknown(blank_slide.shapes, "")
    source = tmp_path / "legacy-shapes.pptx"
    prs.save(source)

    result = extract_pptx_result(source, include_media_placeholders=include_media_placeholders)

    assert result.backend == "python-pptx"
    assert result.partial is False
    assert result.slide_count == 2
    assert result.blank_slides == 1
    assert result.media_objects == 3
    assert "## Slide 1: Recognized heading" in result.markdown
    assert "- Legacy grouped text" in result.markdown
    assert "- Legacy top-level text" in result.markdown
    assert "- [No visible text extracted]" in result.markdown
    assert ("[Image placeholder:" in result.markdown) is include_media_placeholders
    assert ("[Chart placeholder:" in result.markdown) is include_media_placeholders


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
@pytest.mark.parametrize("operation", ["records", "media_count"])
def test_shape_type_classification_preserves_unrelated_errors(error_type, operation) -> None:
    class BrokenShape:
        @property
        def shape_type(self):
            raise error_type("invalid shape data")

    with pytest.raises(error_type, match="invalid shape data"):
        if operation == "records":
            list(extractor.iter_shape_records(BrokenShape()))
        else:
            extractor.shape_media_count(BrokenShape())


@pytest.mark.parametrize("operation", ["records", "media_count"])
def test_unknown_type_does_not_hide_chart_parsing_errors(operation) -> None:
    class BrokenChart:
        @property
        def shape_type(self):
            raise NotImplementedError("unknown shape type")

        @property
        def has_chart(self):
            raise NotImplementedError("unsupported chart data")

    with pytest.raises(NotImplementedError, match="unsupported chart data"):
        if operation == "records":
            list(extractor.iter_shape_records(BrokenChart()))
        else:
            extractor.shape_media_count(BrokenChart())
