"""Regression tests for retrying transient Telegram send failures."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from collections.abc import Callable

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
    finally:
        main.bot_running.clear()

    return logs, skipped, published


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: asyncio.TimeoutError(),
        lambda: aiohttp.ClientConnectionError("connection reset"),
        _network_error,
        _server_error,
        _retry_after_error,
    ],
    ids=["timeout", "aiohttp-network", "telegram-network", "telegram-server-5xx", "rate-limit"],
)
def test_transient_send_failure_leaves_post_eligible_for_retry(monkeypatch, error_factory: Callable[[], Exception]) -> None:
    logs, skipped, published = asyncio.run(_run_failed_send(monkeypatch, error_factory()))

    assert skipped == []
    assert published == []
    assert any(entry[0] == "WARN-406" for entry in logs)


def test_permanent_forbidden_send_failure_stays_skipped(monkeypatch) -> None:
    logs, skipped, published = asyncio.run(_run_failed_send(monkeypatch, _forbidden_error()))

    assert skipped == [("@source", 43, "send_failed")]
    assert published == []
    assert not any(entry[0] == "WARN-406" for entry in logs)
