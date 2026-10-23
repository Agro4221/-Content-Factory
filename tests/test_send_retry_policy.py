"""Regression tests for retrying transient Telegram send failures."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace

import aiohttp
import pytest
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import SendMessage

import main


def _send_message_method() -> SendMessage:
    return SendMessage(chat_id="@target", text="Rewritten text")


def _network_error() -> Exception:
    return TelegramNetworkError(method=_send_message_method(), message="connection reset")


def _server_error() -> Exception:
    return TelegramServerError(method=_send_message_method(), message="server temporarily unavailable")


def _retry_after_error() -> Exception:
    return TelegramRetryAfter(method=_send_message_method(), message="too many requests", retry_after=0)


def _forbidden_error() -> Exception:
    return TelegramForbiddenError(method=_send_message_method(), message="bot was kicked from the chat")


async def _run_failed_send(monkeypatch, error: Exception):
    logs: list[tuple] = []
    skipped: list[tuple] = []
    published: list[tuple] = []

    async def successful_rewrite(*_args, **_kwargs):
        return "общее", "Rewritten text"

    async def no_media(*_args, **_kwargs):
        return [], []

    class FailingBot:
        async def send_message(self, **_kwargs):
            raise error

    monkeypatch.setattr(main, "_ai_rewrite", successful_rewrite)
    monkeypatch.setattr(main, "_download_media", no_media)
    monkeypatch.setattr(main, "_log_send_error", lambda *_args: None)
    monkeypatch.setattr(main, "SLEEP_BETWEEN_POSTS_S", 0)
    monkeypatch.setattr(main.db, "add_log", lambda *args: logs.append(args))
    monkeypatch.setattr(main.db, "save_skipped_post", lambda *args: skipped.append(args))
    monkeypatch.setattr(main.db, "save_post", lambda *args: published.append(args))

    message = SimpleNamespace(id=43, message="Source text", media=None)
    main.bot_running.set()
    stopped_by_send_error = False
    try:
        await main._process_message_group(
            client=None,
            source="@source",
            messages=[message],
            cfg={
                "ads_words": [],
                "watermark": "",
                "topic_channels": {"общее": "@target"},
            },
            bot=FailingBot(),
            ai_session=None,
        )
        stopped_by_send_error = not main.bot_running.is_set()
    finally:
        main.bot_running.clear()

    return logs, skipped, published, stopped_by_send_error


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: TimeoutError(),
        lambda: aiohttp.ClientConnectionError("connection reset"),
        _network_error,
        _server_error,
        _retry_after_error,
    ],
    ids=["timeout", "aiohttp-network", "telegram-network", "telegram-server-5xx", "rate-limit"],
)
def test_transient_send_failure_leaves_post_eligible_for_retry(monkeypatch, error_factory: Callable[[], Exception]) -> None:
    logs, skipped, published, stopped = asyncio.run(_run_failed_send(monkeypatch, error_factory()))

    assert skipped == []
    assert published == []
    assert any(entry[0] == "WARN-406" for entry in logs)
    assert stopped is False


def test_permanent_forbidden_send_failure_stays_retryable_and_stops_parser(monkeypatch) -> None:
    logs, skipped, published, stopped = asyncio.run(
        _run_failed_send(monkeypatch, _forbidden_error())
    )

    assert skipped == []
    assert published == []
    assert stopped is True
    assert any(entry[0] == "ERR-408" for entry in logs)

def test_text_message_failure_does_not_retry_already_sent_media(monkeypatch) -> None:
    """A failed text message must not cause the already sent media to be duplicated."""
    logs: list[tuple[str, str]] = []
    published: list[tuple[str, int]] = []
    queued_followups: list[tuple[str, list[int], str, str]] = []
    skipped: list[tuple[str, int, str]] = []
    photos: list[dict] = []
    messages_sent: list[dict] = []
    long_text = ("source text " * 200).strip()

    class FakeInputMediaPhoto:
        def __init__(self, *, media, caption=None) -> None:
            self.media = media
            self.caption = caption

    class FakeDB:
        def add_log(self, log_type: str, message: str) -> None:
            logs.append((log_type, message))

        def save_post(self, source: str, post_id: int) -> None:
            published.append((source, post_id))

        def save_posts_with_followup(
            self, source: str, post_ids: list[int], target_chat: str, text: str
        ) -> None:
            published.extend((source, post_id) for post_id in post_ids)
            queued_followups.append((source, post_ids, target_chat, text))

        def save_skipped_post(self, source: str, post_id: int, reason: str) -> None:
            skipped.append((source, post_id, reason))

    class Running:
        @staticmethod
        def is_set() -> bool:
            return True

    class FakeBot:
        async def send_photo(self, **kwargs) -> None:
            photos.append(kwargs)

        async def send_message(self, **kwargs) -> None:
            messages_sent.append(kwargs)
            raise RuntimeError("temporary failure sending caption remainder")

    async def successful_rewrite(*_args, **_kwargs) -> tuple[str, str]:
        return "news", long_text

    async def downloaded_media(*_args, **_kwargs):
        return [FakeInputMediaPhoto(media="photo-file", caption="x" * 1020)], []

    monkeypatch.setattr(main, "db", FakeDB())
    monkeypatch.setattr(main, "bot_running", Running())
    monkeypatch.setattr(main, "InputMediaPhoto", FakeInputMediaPhoto)
    monkeypatch.setattr(main, "_ai_rewrite", successful_rewrite)
    monkeypatch.setattr(main, "_download_media", downloaded_media)
    monkeypatch.setattr(main, "SLEEP_BETWEEN_POSTS_S", 0)

    cfg = {
        "ads_words": [],
        "watermark": "",
        "topic_channels": {"news": "@target"},
    }
    source_message = SimpleNamespace(id=72, message="source text", media=object())

    asyncio.run(
        main._process_message_group(
            None, "@source", [source_message], cfg, FakeBot(), None
        )
    )

    assert len(photos) == 1
    assert len(messages_sent) == 1
    assert published == [("@source", 72)]
    assert skipped == []
    assert queued_followups == [("@source", [72], "@target", long_text)]
    assert any(entry[0] == "WARN-407" and "сохранён в SQLite" in entry[1] for entry in logs)

