"""Prevent Telegram HTML parse errors from AI output and watermarks."""
from __future__ import annotations

import main


def test_plain_text_angle_brackets_and_ampersands_are_escaped() -> None:
    assert main._sanitize_telegram_html("5 < 7 & 8 > 3") == "5 &lt; 7 &amp; 8 &gt; 3"


def test_supported_markup_is_preserved_and_unclosed_tags_are_closed() -> None:
    assert main._sanitize_telegram_html("<blockquote>Quoted <b>text") == (
        "<blockquote>Quoted <b>text</b></blockquote>"
    )


def test_unsupported_markup_is_removed_without_losing_safe_text() -> None:
    result = main._sanitize_telegram_html(
        '<script>alert(1)</script><unsupported attr="x">hello</unsupported>'
    )

    assert result == "alert(1)hello"
    assert "<script" not in result
    assert "<unsupported" not in result


def test_unsafe_link_schemes_are_stripped() -> None:
    assert main._sanitize_telegram_html(
        '<a href="javascript:alert(1)">click me</a>'
    ) == "click me"


def test_safe_links_and_language_code_attributes_are_preserved() -> None:
    assert main._sanitize_telegram_html(
        '<a href="https://example.com/?a=1&amp;b=2">open</a>'
        '<pre><code class="language-python">print("ok")'
    ) == (
        '<a href="https://example.com/?a=1&amp;b=2">open</a>'
        '<pre><code class="language-python">print("ok")</code></pre>'
    )
