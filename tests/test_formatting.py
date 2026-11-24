from __future__ import annotations

from formatting import html_to_plain, render_html_chunks, render_telegram_html


def test_renders_bold_from_markdown() -> None:
    text = (
        "Официальный курс юаня НБРБ на 25 ноября 2026:\n\n"
        "- **10 CNY = 4,4918 BYN** (то есть 1 CNY ≈ 0,4492 BYN)"
    )

    assert render_telegram_html(text) == (
        "Официальный курс юаня НБРБ на 25 ноября 2026:\n\n"
        "- <b>10 CNY = 4,4918 BYN</b> (то есть 1 CNY ≈ 0,4492 BYN)"
    )


def test_escapes_html_and_ampersands() -> None:
    rendered = render_telegram_html("<script>alert('x')</script> & **bold**")

    assert rendered == (
        "&lt;script&gt;alert('x')&lt;/script&gt; &amp; <b>bold</b>"
    )
    assert html_to_plain(rendered) == "<script>alert('x')</script> & bold"


def test_fenced_code_is_escaped_and_not_formatted() -> None:
    text = "```python\nvalue = **kwargs & <x>\n```\nи **важно**"

    assert render_telegram_html(text) == (
        "<pre><code>value = **kwargs &amp; &lt;x&gt;</code></pre>\n"
        "и <b>важно</b>"
    )


def test_inline_code_is_protected() -> None:
    assert render_telegram_html("`**literal**` и **bold**") == (
        "<code>**literal**</code> и <b>bold</b>"
    )


def test_safe_link_is_rendered_and_url_escaped() -> None:
    assert render_telegram_html("[ссылка](https://example.com/?a=1&b=2)") == (
        '<a href="https://example.com/?a=1&amp;b=2">ссылка</a>'
    )


def test_unsafe_link_scheme_stays_text() -> None:
    rendered = render_telegram_html("[x](javascript:alert(1))")

    assert "<a href=" not in rendered
    assert "javascript:alert(1)" in html_to_plain(rendered)


def test_headings_quotes_rules_italic_and_strike() -> None:
    text = "# Заголовок\n> цитата\n---\n*курсив* ~~strike~~"

    assert render_telegram_html(text) == (
        "<b>Заголовок</b>\n"
        "<blockquote>цитата</blockquote>\n"
        "────────\n"
        "<i>курсив</i> <s>strike</s>"
    )


def test_chunks_are_within_limit_and_tags_balanced() -> None:
    text = "**bold** " * 1000
    chunks = render_html_chunks(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert all(chunk.count("<b>") == chunk.count("</b>") for chunk in chunks)
    assert all("**" not in chunk for chunk in chunks)


def test_long_fenced_code_is_split_into_balanced_blocks() -> None:
    code = "\n".join(f"line {i}: x = {i} & 1" for i in range(500))
    text = f"До кода:\n\n```python\n{code}\n```\n\nПосле **важно**"
    chunks = render_html_chunks(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert all(
        chunk.count("<pre><code>") == chunk.count("</code></pre>")
        for chunk in chunks
    )
    assert chunks[-1] == "После <b>важно</b>"
    assert "<pre><code>" not in chunks[-1]

    plain = "".join(html_to_plain(chunk) for chunk in chunks)
    for index in (0, 250, 499):
        assert f"line {index}: x = {index} & 1" in plain


def test_long_single_line_code_is_split_losslessly() -> None:
    body = "&" * 5000
    chunks = render_html_chunks(f"```\n{body}\n```")

    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert all(
        chunk.startswith("<pre><code>") and chunk.endswith("</code></pre>")
        for chunk in chunks
    )
    assert "".join(html_to_plain(chunk) for chunk in chunks) == body


def test_empty_text_produces_no_chunks() -> None:
    assert render_telegram_html("") == ""
    assert render_html_chunks("") == []
