"""Regression tests for media-only Telegram posts."""
from __future__ import annotations

import asyncio

import main


class MessageMediaPhoto:
    """Mimic Telegram's photo media class name used by _is_media_skippable."""


class MessageMediaVideo:
    """Mimic Telegram's video media class name used by _is_media_skippable."""


class FakeMessage:
    def __init__(self, message_id: int, media) -> None:
        self.id = message_id
        self.message = ""
        self.media = media


class FakeInputMediaPhoto:
    def __init__(self, *, media, caption=None) -> None:
        self.media = media
        self.caption = caption



class FakeInputMediaVideo:
    def __init__(self, *, media, caption=None) -> None:
        self.media = media
        self.caption = caption


class FakeDB:
    def __init__(self) -> None:
        self.published: list[tuple[str, int]] = []
        self.skipped: list[tuple[str, int, str]] = []
        self.logs: list[tuple[str, str]] = []

    def save_post(self, source: str, post_id: int) -> None:
        self.published.append((source, post_id))

    def save_skipped_post(self, source: str, post_id: int, reason: str) -> None:
        self.skipped.append((source, post_id, reason))

    def add_log(self, log_type: str, message: str) -> None:
        self.logs.append((log_type, message))


class FakeBot:
    def __init__(self) -> None:
        self.photos: list[dict] = []
        self.videos: list[dict] = []
        self.messages: list[dict] = []

    async def send_photo(self, **kwargs) -> None:
        self.photos.append(kwargs)

    async def send_video(self, **kwargs) -> None:
        self.videos.append(kwargs)

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


def test_media_without_caption_is_published_without_calling_ai(monkeypatch) -> None:
    fake_db = FakeDB()
    bot = FakeBot()
    ai_calls: list[str] = []

    async def unexpected_ai_call(raw_text, cfg, session):
        ai_calls.append(raw_text)
        raise AssertionError("AI must not be called when the source post has no text")

    async def fake_download_media(messages, caption):
        assert caption == ""
        return [FakeInputMediaPhoto(media="photo-file", caption=None)], []

    monkeypatch.setattr(main, "db", fake_db)
    monkeypatch.setattr(main, "_ai_rewrite", unexpected_ai_call)
    monkeypatch.setattr(main, "_download_media", fake_download_media)
    monkeypatch.setattr(main, "InputMediaPhoto", FakeInputMediaPhoto)
    monkeypatch.setattr(main, "SLEEP_BETWEEN_POSTS_S", 0)

    cfg = {
        "ads_words": [],
        "watermark": "",
        "topic_channels": {"общее": "@destination"},
    }
    main.bot_running.clear()
    main.bot_running.set()
    try:
        asyncio.run(
            main._process_message_group(
                client=None,
                source="@source",
                messages=[FakeMessage(42, MessageMediaPhoto())],
                cfg=cfg,
                bot=bot,
                ai_session=None,
            )
        )
    finally:
        main.bot_running.clear()

    assert ai_calls == []
    assert fake_db.skipped == []
    assert fake_db.published == [("@source", 42)]
    assert len(bot.photos) == 1
    assert bot.photos[0]["chat_id"] == "@destination"
    assert bot.photos[0]["photo"] == "photo-file"
    assert bot.photos[0]["caption"] is None


def test_video_without_caption_is_published_without_calling_ai(monkeypatch) -> None:
    fake_db = FakeDB()
    bot = FakeBot()
    ai_calls: list[str] = []

    async def unexpected_ai_call(raw_text, cfg, session):
        ai_calls.append(raw_text)
        raise AssertionError("AI must not be called when the source post has no text")

    async def fake_download_media(messages, caption):
        assert caption == ""
        return [FakeInputMediaVideo(media="video-file", caption=None)], []

    monkeypatch.setattr(main, "db", fake_db)
    monkeypatch.setattr(main, "_ai_rewrite", unexpected_ai_call)
    monkeypatch.setattr(main, "_download_media", fake_download_media)
    monkeypatch.setattr(main, "InputMediaVideo", FakeInputMediaVideo)
    monkeypatch.setattr(main, "SLEEP_BETWEEN_POSTS_S", 0)

    cfg = {
        "ads_words": [],
        "watermark": "",
        "topic_channels": {"общее": "@destination"},
    }
    main.bot_running.clear()
    main.bot_running.set()
    try:
        asyncio.run(
            main._process_message_group(
                client=None,
                source="@source",
                messages=[FakeMessage(44, MessageMediaVideo())],
                cfg=cfg,
                bot=bot,
                ai_session=None,
            )
        )
    finally:
        main.bot_running.clear()

    assert ai_calls == []
    assert fake_db.skipped == []
    assert fake_db.published == [("@source", 44)]
    assert bot.videos == [
        {"chat_id": "@destination", "video": "video-file", "caption": None}
    ]
    assert bot.photos == []


def test_truly_empty_message_is_still_skipped(monkeypatch) -> None:
    fake_db = FakeDB()
    bot = FakeBot()

    async def unexpected_download(messages, caption):
        raise AssertionError("A message without text or media must not be downloaded")

    monkeypatch.setattr(main, "db", fake_db)
    monkeypatch.setattr(main, "_download_media", unexpected_download)
    cfg = {"ads_words": [], "watermark": "", "topic_channels": {"общее": "@destination"}}
    main.bot_running.clear()
    main.bot_running.set()
    try:
        asyncio.run(
            main._process_message_group(
                client=None,
                source="@source",
                messages=[FakeMessage(43, None)],
                cfg=cfg,
                bot=bot,
                ai_session=None,
            )
        )
    finally:
        main.bot_running.clear()

    assert fake_db.skipped == [("@source", 43, "empty")]
    assert fake_db.published == []
    assert bot.photos == []

def test_unsupported_media_without_text_is_skipped_instead_of_sending_empty_text(monkeypatch) -> None:
    class MessageMediaPoll:
        pass

    fake_db = FakeDB()
    bot = FakeBot()

    async def unexpected_download(messages, caption):
        raise AssertionError("unsupported media must not be passed to the downloader")

    async def unexpected_ai(*_args, **_kwargs):
        raise AssertionError("a media-only poll must not call AI")

    monkeypatch.setattr(main, "db", fake_db)
    monkeypatch.setattr(main, "_download_media", unexpected_download)
    monkeypatch.setattr(main, "_ai_rewrite", unexpected_ai)
    cfg = {
        "ads_words": [],
        "watermark": "",
        "topic_channels": {"общее": "@destination"},
    }
    main.bot_running.clear()
    main.bot_running.set()
    try:
        asyncio.run(
            main._process_message_group(
                None, "@source", [FakeMessage(45, MessageMediaPoll())], cfg, bot, None
            )
        )
    finally:
        main.bot_running.clear()

    assert fake_db.skipped == [("@source", 45, "unsupported_media")]
    assert fake_db.published == []
    assert bot.messages == []
    assert bot.photos == []
    assert bot.videos == []

