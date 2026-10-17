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
        self.message = "source caption"
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


def test_long_media_caption_is_sent_as_caption_plus_continuation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    final_text = "Длинный текст публикации с подробностями. " * 50
    normalized = final_text.strip()
    displayed_prefix = normalized[:1020].rsplit(" ", 1)[0]
    expected_caption = displayed_prefix + "…"
    expected_remainder = normalized[len(displayed_prefix):].lstrip()

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
    assert bot.photo["caption"] == expected_caption
    assert len(bot.photo["caption"]) <= 1024
    assert bot.texts == [expected_remainder]
    assert db.published == [("@source", 17)]
    assert not db.logs
