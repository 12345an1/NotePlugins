from scripts.clean_latex_from_ppt import clean_text

from pathlib import Path


def test_clean_text_removes_noise_and_normalizes_latex():
    raw = "α ≤ β\u200b\n\\\\frac {x}{y}\x00\nnormal text\n"
    cleaned = clean_text(raw, unicode_math=True)

    assert r"\alpha" in cleaned
    assert r"\le" in cleaned
    assert r"\beta" in cleaned
    assert r"\frac" in cleaned
    assert "\u200b" not in cleaned
    assert "\x00" not in cleaned


def test_formula_regression_fixture_normalizes_pdf_extraction_noise():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "pdf-formula-regression" / "extracted_formula_noise.md"
    cleaned = clean_text(fixture.read_text(encoding="utf-8"), unicode_math=True)

    assert r"\alpha" in cleaned
    assert r"\le" in cleaned
    assert r"\beta" in cleaned
    assert r"\frac" in cleaned
    assert r"\sum" in cleaned
    assert "\u200b" not in cleaned
