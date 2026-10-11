from pathlib import Path

from scripts.check_obsidian_links import check_links


def test_check_links_accepts_markdown_wiki_alias_and_stem(tmp_path: Path):
    (tmp_path / "course").mkdir()
    (tmp_path / "course" / "a.md").write_text(
        "\n".join(
            [
                "# A",
                "[B](b.md)",
                "[[course/b|B alias]]",
                "[[b]]",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "course" / "b.md").write_text("# B\n", encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 3
    assert broken == []
    assert self_links == []


def test_check_links_reports_broken_and_self_links(tmp_path: Path):
    page = tmp_path / "a.md"
    page.write_text("[missing](missing.md)\n[[a]]\n", encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 2
    assert len(broken) == 1
    assert broken[0].target == "missing.md"
    assert len(self_links) == 1
    assert self_links[0].target == "a"


def test_check_links_accepts_spaces_url_encoding_anchors_and_root_paths(tmp_path: Path):
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "My Note.md").write_text(
        "# My Note\n\n## Section\n",
        encoding="utf-8",
    )
    (tmp_path / "index.md").write_text("# Index\n", encoding="utf-8")
    (tmp_path / "folder" / "topic.md").write_text(
        "\n".join(
            [
                "[Encoded](My%20Note.md#section)",
                "[Root](/folder/My%20Note.md?query=1)",
                "[[folder/My Note#Section|Wiki alias]]",
                "[Parent](../index.md)",
            ]
        ),
        encoding="utf-8",
    )

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 4
    assert broken == []
    assert self_links == []


def test_check_links_accepts_balanced_parentheses_in_markdown_destination(tmp_path: Path):
    target = tmp_path / "topic(1).md"
    target.write_text("# Topic\n", encoding="utf-8")
    (tmp_path / "index.md").write_text("[Topic](topic(1).md)\n", encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 1
    assert broken == []
    assert self_links == []


def test_check_links_accepts_commonmark_destination_forms_and_titles(tmp_path: Path):
    for name in (
        "foo(and(bar)).md",
        "escaped(1).md",
        "folder/My Note(1).md",
        "encoded(1).md",
        "literal#name.md",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = "# Target\n"
        if name == "encoded(1).md":
            content += "\n## section\n"
        target.write_text(content, encoding="utf-8")
    (tmp_path / "index.md").write_text(
        "\n".join(
            [
                '[Nested](foo(and(bar)).md "nested title")',
                r"[Escaped](escaped\(1\).md 'escaped title')",
                "[Spaced](<folder/My Note(1).md> (spaced title))",
                "[Encoded](encoded%281%29.md#section)",
                "[Encoded hash](literal%23name.md)",
                '[External](https://example.com/a%28b%29 "web")',
                "![Image](missing(and(bar)).png)",
                "[Empty]( \"title only\")",
                "[Malformed](foo(and(bar))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 5
    assert broken == []
    assert self_links == []


def test_check_links_treats_quoted_token_as_bare_destination(tmp_path: Path):
    target = tmp_path / '"quoted".md'
    target.write_text("# Quoted\n", encoding="utf-8")
    (tmp_path / "index.md").write_text('[Quoted]( "quoted".md)\n', encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 1
    assert broken == []
    assert self_links == []


def test_check_links_rejects_label_that_crosses_a_blank_line(tmp_path: Path):
    (tmp_path / "target.md").write_text("# Target\n", encoding="utf-8")
    (tmp_path / "index.md").write_text("[blank\n\nlabel](target.md)\n", encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 0
    assert broken == []
    assert self_links == []


def test_root_relative_link_does_not_fall_back_to_source_directory(tmp_path: Path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "target.md").write_text("# Nested target\n", encoding="utf-8")
    (nested / "source.md").write_text("[Root target](/target.md)\n", encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 1
    assert [issue.target for issue in broken] == ["/target.md"]
    assert self_links == []


def test_check_links_checks_local_anchors_but_ignores_external_and_images(tmp_path: Path):
    page = tmp_path / "a.md"
    page.write_text(
        "\n".join(
            [
                "# Local",
                "[Web](https://example.com/missing.md)",
                "[Mail](mailto:test@example.com)",
                "[App](obsidian://open?vault=x)",
                "[Uppercase web](HTTPS://example.com/resource)",
                "[Protocol relative](//example.com/resource)",
                "[Custom scheme](vscode://file/example.md)",
                "[Anchor](#local)",
                "![Image](missing.png)",
            ]
        ),
        encoding="utf-8",
    )

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 1
    assert broken == []
    assert self_links == []


def test_check_links_reports_missing_heading_anchors(tmp_path: Path):
    target = tmp_path / "target.md"
    target.write_text("# Present\n", encoding="utf-8")
    (tmp_path / "index.md").write_text(
        "[Present](target.md#present)\n"
        "[Missing](target.md#missing)\n"
        "[Local](#missing-local)\n",
        encoding="utf-8",
    )

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 3
    assert [issue.target for issue in broken] == ["target.md#missing", "#missing-local"]
    assert self_links == []


def test_check_links_accepts_chinese_encoded_and_duplicate_heading_anchors(tmp_path: Path):
    (tmp_path / "target.md").write_text(
        "# 中文 标题\n## 重复 标题\n## 重复 标题\n",
        encoding="utf-8",
    )
    (tmp_path / "index.md").write_text(
        "[unencoded](target.md#中文-标题)\n"
        "[encoded](target.md#%E4%B8%AD%E6%96%87-%E6%A0%87%E9%A2%98)\n"
        "[duplicate](target.md#重复-标题-1)\n"
        "[missing](target.md#重复-标题-2)\n",
        encoding="utf-8",
    )

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 4
    assert [issue.target for issue in broken] == ["target.md#重复-标题-2"]
    assert self_links == []


def test_check_links_does_not_check_markdown_pseudo_links_inside_code(tmp_path: Path):
    (tmp_path / "index.md").write_text(
        "```markdown\n[false](missing.md#missing)\n```\n"
        "[true](#visible)\n# Visible\n"
        "[external](https://example.com/missing.md#missing)\n",
        encoding="utf-8",
    )

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 1
    assert broken == []
    assert self_links == []


def test_check_links_ignores_links_inside_fenced_and_inline_code(tmp_path: Path):
    page = tmp_path / "a.md"
    page.write_text(
        "`arr[[1]]`\n\n```python\nself.branches[0](x)\ntriton_kernel[grid](x, BLOCK=block)\n```\n",
        encoding="utf-8",
    )

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 0
    assert broken == []
    assert self_links == []


def test_check_links_ignores_fence_closed_by_a_longer_fence(tmp_path: Path):
    page = tmp_path / "a.md"
    page.write_text("```python\n[[missing]]\n````\n[[also-missing]]\n", encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 1
    assert [issue.target for issue in broken] == ["also-missing"]
    assert self_links == []


def test_check_links_ignores_four_space_indented_code(tmp_path: Path):
    page = tmp_path / "a.md"
    page.write_text("    [[missing]]\n\t[also-missing](no.md)\n", encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 0
    assert broken == []
    assert self_links == []


def test_check_links_checks_four_space_list_continuation_as_body_text(tmp_path: Path):
    page = tmp_path / "a.md"
    page.write_text("- Body item\n    [missing](missing.md)\n", encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 1
    assert [issue.target for issue in broken] == ["missing.md"]
    assert self_links == []


def test_check_links_masks_inline_spans_with_different_backtick_lengths(tmp_path: Path):
    page = tmp_path / "a.md"
    page.write_text("``code ` [[inside]] ``\n[[outside]]\n", encoding="utf-8")

    broken, self_links, checked = check_links(tmp_path)

    assert checked == 1
    assert [issue.target for issue in broken] == ["outside"]
    assert self_links == []


def test_check_links_rejects_targets_that_escape_vault_root(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "course").mkdir(parents=True)
    (tmp_path / "outside.md").write_text("# Outside\n", encoding="utf-8")
    (vault / "course" / "lesson.md").write_text("[outside](../../outside.md)\n", encoding="utf-8")

    broken, self_links, checked = check_links(vault)

    assert checked == 1
    assert [issue.target for issue in broken] == ["../../outside.md"]
    assert self_links == []
