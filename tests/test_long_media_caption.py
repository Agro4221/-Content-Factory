"""Regression tests for preserving text beyond Telegram media caption limits."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import main


class MessageMediaPhoto:
    pass


class FakePhotoMessage:
    def __init__(self, message_id: int, path: Path) -> None:
        self.id = message_id
        self.message = "Длинный текст публикации с подробностями"
        self.media = MessageMediaPhoto()
        self.path = path

    async def download_media(self, *, file: str, progress_callback):
        self.path.write_bytes(b"image")
        return str(self.path)


class FakeDB:
    def __init__(self) -> None:
        self.published: list[tuple[str, int]] = []
        self.logs: list[tuple[str, str]] = []

    def save_post(self, source: str, post_id: int) -> None:
        self.published.append((source, post_id))

    def add_log(self, log_type: str, message: str) -> None:
        self.logs.append((log_type, message))

    def save_skipped_post(self, *_args, **_kwargs) -> None:
        raise AssertionError("successful long-caption post should not be skipped")


class Running:
    @staticmethod
    def is_set() -> bool:
        return True


class FakeBot:
    def __init__(self) -> None:
        self.photo: dict | None = None
        self.texts: list[str] = []

    async def send_photo(self, **kwargs) -> None:
        self.photo = kwargs

    async def send_video(self, **kwargs) -> None:
        raise AssertionError("test media is a photo")

    async def send_media_group(self, **kwargs) -> None:
        raise AssertionError("test contains a single photo")

    async def send_message(self, **kwargs) -> None:
        self.texts.append(kwargs["text"])


def test_caption_remainder_helper_respects_1024_character_limit() -> None:
    assert main._media_caption_remainder("short text") == ""

    text = "word " * 300
    normalized = text.strip()
    prefix = normalized[:1020].rsplit(" ", 1)[0]

    assert main._media_caption_remainder(text) == normalized[len(prefix):].lstrip()


def test_long_text_is_sent_as_single_message_beside_media(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Текст длиннее подписи (1024) не должен рваться надвое: картинка
    # уходит без подписи, текст — одним сообщением целиком.
    final_text = ("Длинный текст публикации с подробностями. " * 50).strip()

    db = FakeDB()
    bot = FakeBot()
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", Running())
    monkeypatch.setattr(main, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(main, "SLEEP_BETWEEN_POSTS_S", 0)

    async def fake_ai_rewrite(*_args, **_kwargs) -> tuple[str, str]:
        return "news", final_text

    monkeypatch.setattr(main, "_ai_rewrite", fake_ai_rewrite)

    cfg = {
        "ads_words": [],
        "watermark": "",
        "topic_channels": {"news": "@target"},
    }
    message = FakePhotoMessage(17, tmp_path / "photo.jpg")

    asyncio.run(
        main._process_message_group(None, "@source", [message], cfg, bot, None)
    )

    assert bot.photo is not None
    assert bot.photo["chat_id"] == "@target"
    # Подписи у картинки нет: текст не помещается в 1024 символа.
    assert bot.photo["caption"] is None
    # Текст уходит ровно одним сообщением и не делится.
    assert bot.texts == [final_text]
    assert db.published == [("@source", 17)]
    assert len(db.logs) == 1
    assert db.logs[0][0] == "OK-501"

def test_unbroken_long_caption_keeps_prefix_and_sends_only_the_tail() -> None:
    text = "X" * 1500

    prefix = main._media_caption_prefix(text)
    remainder = main._media_caption_remainder(text)

    assert len(prefix) == 1020
    assert prefix == text[:1020]
    assert remainder == text[1020:]
    assert len(prefix + "…") <= 1024
    assert prefix + remainder == text

def test_long_html_caption_is_truncated_without_broken_tags() -> None:
    text = "<blockquote><b>" + ("Verylongword " * 140) + "</b></blockquote>"

    prefix = main._media_caption_prefix(text)
    remainder = main._media_caption_remainder(text)
    caption = prefix + "…"

    assert len(caption) <= 1024
    assert prefix.startswith("<blockquote><b>")
    assert prefix.endswith("</b></blockquote>")
    assert main._sanitize_telegram_html(caption) == caption
    assert "</b>" not in remainder
    assert "</blockquote>" not in remainder


def test_truncation_does_not_split_html_tags_or_entities() -> None:
    for text in [
        "<a href=\"https://example.com/some/very/long/path?one=1&amp;two=2\">" + "x" * 1200 + "</a>",
        ("word " * 190) + "&amp;" + ("tail " * 100),
    ]:
        prefix = main._media_caption_prefix(text)
        assert len(prefix) <= 1020
        assert main._sanitize_telegram_html(prefix) == prefix

def test_long_html_caption_prefix_and_remainder_have_no_content_gap() -> None:
    body = "x" * 1500
    text = "<blockquote><b>" + body + "</b></blockquote>"

    prefix = main._media_caption_prefix(text)
    remainder = main._media_caption_remainder(text)
    visible_prefix = prefix.replace("<blockquote>", "").replace("</blockquote>", "")
    visible_prefix = visible_prefix.replace("<b>", "").replace("</b>", "")

    assert visible_prefix + remainder == body
    assert len(prefix + "…") <= 1024



def test_long_nested_html_caption_preserves_all_visible_content() -> None:
    from html.parser import HTMLParser

    class VisibleText(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            self.parts.append(data)

        def visible(self, fragment: str) -> str:
            self.feed(fragment)
            self.close()
            return "".join(self.parts)

    source = main._sanitize_telegram_html(
        "<blockquote><b>"
        + ("Первое &amp; второе <i>третье и четвёртое</i> " * 120)
        + "</b></blockquote>"
    )
    prefix = main._media_caption_prefix(source)
    remainder = main._media_caption_remainder(source)

    expected = VisibleText().visible(source)
    actual = VisibleText().visible(prefix) + VisibleText().visible(remainder)

    # The boundary whitespace is intentionally omitted because the tail is a
    # separate Telegram message. Every non-whitespace visible character must
    # still survive the split, including decoded HTML entities.
    assert "".join(actual.split()) == "".join(expected.split())
    assert len(prefix + "…") <= 1024
    assert main._sanitize_telegram_html(prefix) == prefix
