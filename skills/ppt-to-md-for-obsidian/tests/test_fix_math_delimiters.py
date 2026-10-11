from scripts.fix_math_delimiters import convert_math_delimiters, normalize_dollar_blocks


def test_inline_paren_converted():
    out, n = convert_math_delimiters(r"mass is \(m\) here")
    assert out == "mass is $m$ here"
    assert n == 2


def test_single_line_display_converted():
    out, n = convert_math_delimiters(r"before \[a^2+b^2=c^2\] after")
    assert out == "before $$a^2+b^2=c^2$$ after"
    assert n == 2


def test_multiline_display_converted():
    src = "\\[\n\\dot{x}=Ax+Bu\n\\]\n"
    out, n = convert_math_delimiters(src)
    assert out == "$$\n\\dot{x}=Ax+Bu\n$$\n"
    assert n == 2


def test_existing_dollar_math_untouched():
    src = "$$\nL(\\theta)=\\frac{1}{N}\n$$\n"
    out, n = convert_math_delimiters(src)
    assert out == src
    assert n == 0


def test_fenced_code_block_untouched():
    src = "```text\n\\[ x \\] and \\( y \\)\n```\n"
    out, n = convert_math_delimiters(src)
    assert out == src
    assert n == 0


def test_inline_code_span_untouched():
    out, n = convert_math_delimiters(r"literal `\[ x \]` stays")
    assert out == r"literal `\[ x \]` stays"
    assert n == 0


def test_mixed_content():
    src = (
        "Inline \\(a\\) and display:\n"
        "\\[\nE=mc^2\n\\]\n"
        "Then \\(b\\).\n"
    )
    out, n = convert_math_delimiters(src)
    assert out == (
        "Inline $a$ and display:\n"
        "$$\nE=mc^2\n$$\n"
        "Then $b$.\n"
    )
    assert n == 6


def test_normalize_inline_block_to_own_lines():
    out, n = normalize_dollar_blocks("$$E=mc^2$$\n")
    assert out == "$$\nE=mc^2\n$$\n"
    assert n == 1


def test_normalize_line_with_surrounding_text():
    out, n = normalize_dollar_blocks("见 $$x^2$$ 结束\n")
    assert out == "见\n$$\nx^2\n$$\n结束\n"
    assert n == 1


def test_normalize_already_block_untouched():
    src = "$$\nE=mc^2\n$$\n"
    out, n = normalize_dollar_blocks(src)
    assert out == src
    assert n == 0


def test_normalize_skips_fenced_code():
    src = "```text\n$$x$$\n```\n"
    out, n = normalize_dollar_blocks(src)
    assert out == src
    assert n == 0


def test_normalize_skips_inline_code():
    src = "literal `$$x$$` stays\n"
    out, n = normalize_dollar_blocks(src)
    assert out == src
    assert n == 0


def test_normalize_multiple_pairs():
    out, n = normalize_dollar_blocks("$$a$$ and $$b$$\n")
    assert out == "$$\na\n$$\nand\n$$\nb\n$$\n"
    assert n == 2


def test_combined_legacy_then_normalize():
    converted, _ = convert_math_delimiters(r"before \[a^2\] after" + "\n")
    out, n = normalize_dollar_blocks(converted)
    assert out == "before\n$$\na^2\n$$\nafter\n"
    assert n == 1
