"""Публикация по тегам: пост уходит во все каналы с подходящими тегами.

Каналу можно задать несколько тегов. Если один и тот же тег стоит у
нескольких каналов, пост уходит во все — так работает маршрутизация по
тегам вместо прежних категорий.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import main


class FakeDB:
    def __init__(self) -> None:
        self.published: list[tuple[str, int]] = []
        self.logs: list[tuple[str, str]] = []

    def add_log(self, log_type: str, message: str) -> None:
        self.logs.append((log_type, message))

    def get_setting(self, key: str, default: str = "") -> str:
        return default

    def save_posts(self, source: str, post_ids: list[int]) -> None:
        for post_id in post_ids:
            self.published.append((source, post_id))

    def save_post(self, source: str, post_id: int) -> None:
        self.published.append((source, post_id))

    def is_published(self, _source: str, _post_id: int) -> bool:
        return False

    def is_skipped(self, _source: str, _post_id: int) -> bool:
        return False


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, chat_id, text, **_kwargs):
        self.sent.append({"kind": "message", "chat_id": chat_id, "text": text})

    async def send_photo(self, chat_id, photo, caption=None, **_kwargs):
        self.sent.append({"kind": "photo", "chat_id": chat_id, "caption": caption})

    async def send_video(self, chat_id, video, caption=None, **_kwargs):
        self.sent.append({"kind": "video", "chat_id": chat_id, "caption": caption})

    async def send_document(self, chat_id, document, caption=None, **_kwargs):
        self.sent.append({"kind": "document", "chat_id": chat_id, "caption": caption})

    async def send_media_group(self, chat_id, media, **_kwargs):
        self.sent.append({"kind": "group", "chat_id": chat_id, "count": len(media)})


class Running:
    @staticmethod
    def is_set() -> bool:
        return True


def _run(monkeypatch, routes: dict, tags: list[str], text: str = "Новость") -> FakeBot:
    bot = FakeBot()
    db = FakeDB()

    async def fake_ai_rewrite(*_args, **_kwargs):
        return tags, text

    async def fake_download(*_args, **_kwargs):
        return [], []

    async def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", Running())
    monkeypatch.setattr(main, "_ai_rewrite", fake_ai_rewrite)
    monkeypatch.setattr(main, "_download_media", fake_download)
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(main, "SLEEP_BETWEEN_POSTS_S", 0)

    message = SimpleNamespace(id=7, message="исходный текст", media=None)
    cfg = {
        "ads_words": [],
        "watermark": "",
        "publish_mode": "auto",
        "tag_routes": routes,
        "tag_routes_tags": main.routing.all_tags(routes),
    }
    asyncio.run(main._process_message_group(None, "@source", [message], cfg, bot, None))
    return bot


def test_post_goes_to_every_channel_with_matching_tag(monkeypatch) -> None:
    bot = _run(
        monkeypatch,
        {"@a": ["politics"], "@b": ["politics"], "@c": ["sport"]},
        ["politics"],
    )
    assert [item["chat_id"] for item in bot.sent] == ["@a", "@b"]


def test_channel_with_several_tags_receives_post_by_any_of_them(monkeypatch) -> None:
    bot = _run(monkeypatch, {"@a": ["politics", "urgent"]}, ["urgent"])
    assert [item["chat_id"] for item in bot.sent] == ["@a"]


def test_post_without_matching_tags_goes_to_fallback_channel(monkeypatch) -> None:
    bot = _run(monkeypatch, {"@a": ["politics"], "@fallback": ["общее"]}, ["sport"])
    assert [item["chat_id"] for item in bot.sent] == ["@fallback"]


def test_post_without_any_route_is_not_published(monkeypatch) -> None:
    bot = _run(monkeypatch, {"@a": ["politics"]}, ["sport"])
    assert bot.sent == []


def test_same_post_is_sent_to_several_channels(monkeypatch) -> None:
    bot = _run(monkeypatch, {"@a": ["politics"], "@b": ["politics"]}, ["politics"])
    assert len(bot.sent) == 2
    assert all(item["text"] == "Новость" for item in bot.sent)


def test_queued_post_keeps_all_target_channels(monkeypatch, tmp_path) -> None:
    """В очередь попадают все подходящие каналы."""
    saved: dict = {}

    def fake_save(source, messages, publish_text, target_chats, category, media_list, temp_paths):
        saved["chats"] = list(target_chats)
        saved["category"] = category
        return 1

    monkeypatch.setattr(main, "db", FakeDB())
    monkeypatch.setattr(main, "bot_running", Running())
    monkeypatch.setattr(main, "_save_post_to_queue", fake_save)

    async def fake_ai_rewrite(*_args, **_kwargs):
        return ["politics"], "Новость"

    async def fake_download(*_args, **_kwargs):
        return [], []

    async def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(main, "_ai_rewrite", fake_ai_rewrite)
    monkeypatch.setattr(main, "_download_media", fake_download)
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)

    message = SimpleNamespace(id=7, message="исходный текст", media=None)
    cfg = {
        "ads_words": [],
        "watermark": "",
        "publish_mode": "manual",
        "tag_routes": {"@a": ["politics"], "@b": ["politics"]},
        "tag_routes_tags": ["politics"],
    }
    asyncio.run(main._process_message_group(None, "@source", [message], cfg, FakeBot(), None))

    assert saved["chats"] == ["@a", "@b"]
    assert saved["category"] == "politics"


def test_queued_post_reads_all_channels_from_database_row() -> None:
    post = {
        "target_chat": "@a",
        "target_chats": json.dumps(["@a", "@b"]),
    }
    assert main._queued_target_chats(post) == ["@a", "@b"]


def test_queued_post_falls_back_to_single_channel() -> None:
    post = {"target_chat": "@a", "target_chats": "[]"}
    assert main._queued_target_chats(post) == ["@a"]
