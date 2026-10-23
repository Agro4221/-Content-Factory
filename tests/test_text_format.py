"""Проверяет перевод Markdown в HTML Telegram."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import text_format


def test_bold_and_italic() -> None:
    assert text_format.markdown_to_telegram_html("**жирно**") == "<b>жирно</b>"
    assert text_format.markdown_to_telegram_html("*курсив*") == "<i>курсив</i>"
    assert text_format.markdown_to_telegram_html("__жирно__") == "<b>жирно</b>"
    assert text_format.markdown_to_telegram_html("~~зачёркнуто~~") == "<s>зачёркнуто</s>"


def test_quote_becomes_blockquote() -> None:
    result = text_format.markdown_to_telegram_html("> Важная цитата")
    assert result == "<blockquote>Важная цитата</blockquote>"


def test_code_inline_and_block() -> None:
    assert text_format.markdown_to_telegram_html("`код`") == "<code>код</code>"
    block = text_format.markdown_to_telegram_html("```python\nprint(1)\n```")
    assert block == "<pre>print(1)</pre>"


def test_link_becomes_anchor() -> None:
    result = text_format.markdown_to_telegram_html("Ссылка [тут](https://example.com)")
    assert result == 'Ссылка <a href="https://example.com">тут</a>'


def test_plain_text_is_escaped() -> None:
    result = text_format.markdown_to_telegram_html("5 < 7 & 8 > 2")
    assert "<" not in result
    assert "&lt;" in result and "&amp;" in result and "&gt;" in result


def test_existing_html_is_preserved() -> None:
    source = "Готово <b>уже</b> и <a href=\"https://example.com\">ссылка</a>"
    result = text_format.markdown_to_telegram_html(source)
    assert "<b>уже</b>" in result
    assert '<a href="https://example.com">ссылка</a>' in result


def test_markdown_inside_html_is_converted() -> None:
    result = text_format.markdown_to_telegram_html("<b>жирно</b> и **ещё**")
    assert "<b>жирно</b>" in result
    assert "<b>ещё</b>" in result


def test_empty_text() -> None:
    assert text_format.markdown_to_telegram_html("") == ""


def test_underscores_inside_words_are_kept() -> None:
    """Подчёркивания внутри слова — это не курсив."""
    result = text_format.markdown_to_telegram_html("some_var_name")
    assert "<i>" not in result


def test_guillemets_become_blockquote() -> None:
    """Прямая речь в «ёлочках» оформляется цитатой, даже если модель забыла теги."""
    result = text_format.markdown_to_telegram_html(
        "Он заявил, что «ремонт закончится в июне», и ушёл."
    )
    assert result == (
        "Он заявил, что <blockquote>ремонт закончится в июне</blockquote>, и ушёл."
    )


def test_bare_url_becomes_link() -> None:
    result = text_format.markdown_to_telegram_html(
        "Подробности: https://example.com/news/1"
    )
    assert '<a href="https://example.com/news/1">' in result


def test_url_inside_existing_link_is_not_double_wrapped() -> None:
    result = text_format.markdown_to_telegram_html(
        "[источник](https://example.com/news)"
    )
    assert result.count('<a href=') == 1
