"""Подготовка текста к отправке в формате HTML Telegram.

Преобразует распространённые элементы Markdown в поддерживаемые Telegram
теги. Уже оформленные HTML-теги сохраняются; Markdown обрабатывается в
обычных текстовых фрагментах.

Поддерживаются:

* ``**жирный**`` и ``__жирный__`` → ``<b>``;
* ``*курсив*`` и ``_курсив_`` → ``<i>``;
* ``~~зачёркнутый~~`` → ``<s>``;
* текст в обратных кавычках → ``<code>``;
* блоки кода с необязательным указанием языка → ``<pre>``;
* строки ``> цитата`` → ``<blockquote>``;
* ссылки ``[текст](https://ссылка)`` → ``<a href="...">``.

Строки-разделители ``---`` остаются текстом, поскольку Telegram не
поддерживает HTML-тег ``<hr>``. Существующие допустимые HTML-теги сохраняются.
"""
from __future__ import annotations

import html
import re

#: Теги, которые Telegram понимает и которые разрешены в постах.
_TELEGRAM_TAGS = (
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre", "a", "blockquote", "tg-spoiler", "tg-emoji",
)

#: Разметка, которая уже в готовом HTML — её не трогаем.
_HTML_TAG_RE = re.compile(
    r"</?(?:" + "|".join(_TELEGRAM_TAGS) + r")\b[^>]*>",
    re.IGNORECASE,
)

#: Кусок текста, который нельзя форматировать (код, готовые теги).
_PROTECTED_RE = re.compile(
    r"(?P<pre>```.*?```)"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<tag></?(?:" + "|".join(_TELEGRAM_TAGS) + r")\b[^>]*>)",
    re.IGNORECASE | re.DOTALL,
)

_FENCE_RE = re.compile(r"^```[A-Za-z0-9_+-]{0,20}\s*$")


def _inline_markdown(fragment: str) -> str:
    """Переводит inline-Markdown в HTML Telegram."""
    text = html.escape(fragment, quote=False)

    # Ссылка: [текст](url). Текст уже экранирован, url тоже.
    text = re.sub(
        r"\[([^\]\n]{1,200})\]\(\s*(https?://[^\s)]+)\s*\)",
        lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>',
        text,
    )

    # Жирный: **текст** и __текст__
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<b>\1</b>", text, flags=re.DOTALL)

    # Зачёркнутый: ~~текст~~
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    # Курсив: *текст* и _текст_ (одиночные символы)
    text = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]{1,300}?)(?<=\S)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![\w_])_(?=\S)([^_\n]{1,300}?)(?<=\S)_(?![\w_])", r"<i>\1</i>", text)

    # Прямая речь в «ёлочках» — цитата. Модели часто не ставят теги,
    # поэтому цитату оформляем сами.
    text = re.sub(
        r"«([^«»\n]{3,600})»",
        lambda m: f"<blockquote>{m.group(1)}</blockquote>",
        text,
    )

    # Голый адрес в тексте — делаем ссылкой. Адрес внутри уже готовой ссылки
    # не трогаем: он стоит после кавычки.
    return re.sub(
        r'(?<!["\'>])((?:https?://)[^\s<>"\'\]]+)',
        lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>',
        text,
    )


def _convert_block(lines: list[str]) -> str:
    """Переводит блоки текста: цитаты, блоки кода, абзацы."""
    out: list[str] = []
    in_code = False
    code_lines: list[str] = []
    quote_lines: list[str] = []

    def flush_quote() -> None:
        if quote_lines:
            out.append(
                "<blockquote>" + _inline_markdown(" ".join(quote_lines)) + "</blockquote>"
            )
            quote_lines.clear()

    for line in lines:
        stripped = line.strip()

        if in_code:
            if _FENCE_RE.match(stripped):
                out.append(
                    "<pre>" + html.escape("\n".join(code_lines), quote=False) + "</pre>"
                )
                code_lines.clear()
                in_code = False
            else:
                code_lines.append(line)
            continue

        if _FENCE_RE.match(stripped):
            flush_quote()
            in_code = True
            continue

        if stripped.startswith(">"):
            quote_lines.append(stripped.lstrip(">").strip())
            continue

        flush_quote()

        if not stripped:
            out.append("")
            continue

        out.append(_inline_markdown(line))

    flush_quote()
    if in_code and code_lines:
        out.append("<pre>" + html.escape("\n".join(code_lines), quote=False) + "</pre>")

    return "\n".join(out)


def markdown_to_telegram_html(text: str) -> str:
    """Переводит Markdown-разметку модели в HTML Telegram.

    Готовые теги Telegram сохраняются как есть, Markdown переводится.
    Текст без разметки остаётся текстом — экранируется, чтобы спецсимволы
    не превратились в случайные HTML-теги.
    """
    if not text:
        return ""

    # Готовые блоки кода и теги не трогаем: сохраняем их как «защищённые».
    protected: list[tuple[str, str]] = []

    def _stash(match: re.Match[str]) -> str:
        whole = match.group(0)
        if match.group("pre") is not None:
            protected.append(("pre", whole))
        elif match.group("code") is not None:
            protected.append(("code", whole))
        else:
            protected.append(("tag", whole))
        return f"\x00{len(protected) - 1}\x00"

    working = _PROTECTED_RE.sub(_stash, text)

    working = _convert_block(working.split("\n"))

    for index, (kind, chunk) in enumerate(protected):
        if kind == "pre":
            body = chunk.strip()
            if body.startswith("```") and body.endswith("```"):
                body = body[3:-3]
            body = body.removeprefix("\n")
            body = body.removesuffix("\n")
            # Строка с языком программирования — служебная, в разметку не идёт.
            lines = body.split("\n")
            if lines and re.fullmatch(r"[A-Za-z0-9_+-]{1,20}", lines[0].strip()):
                lines = lines[1:]
            body = "\n".join(lines)
            restored = "<pre>" + html.escape(body, quote=False) + "</pre>"
        elif kind == "code":
            body = chunk[1:-1]
            restored = "<code>" + html.escape(body, quote=False) + "</code>"
        else:
            restored = chunk
        working = working.replace(f"\x00{index}\x00", restored)

    return working
