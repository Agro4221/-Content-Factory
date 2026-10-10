"""Регрессии для отказобезопасной обработки AI-ошибок."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import main


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None, body: str = "") -> None:
        self.status = status
        self.payload = payload or {}
        self.body = body

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def json(self) -> dict:
        return self.payload

    async def text(self) -> str:
        return self.body


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def post(self, *_args, **_kwargs) -> FakeResponse:
        return self.response


def ai_config() -> dict:
    return {
        "topic_channels": {"общее": "@target"},
        "ai_mode": "REMOTE",
        "remote_url": "https://ai.invalid/v1/chat/completions",
        "remote_model": "test-model",
        "remote_api_key": "",
    }


def test_http_error_raises_instead_of_returning_source_text(monkeypatch) -> None:
    monkeypatch.setattr(main.db, "get_setting", lambda *_args: "")
    monkeypatch.setattr(main.db, "add_log", lambda *_args: None)
    monkeypatch.setattr(main, "_log_ai_http_error", lambda *_args: None)
    session = FakeSession(FakeResponse(503, body="service unavailable"))

    with pytest.raises(main.AIRewriteError):
        asyncio.run(main._ai_rewrite("original source text", ai_config(), session))


def test_empty_ai_response_is_not_treated_as_a_rewrite(monkeypatch) -> None:
    monkeypatch.setattr(main.db, "get_setting", lambda *_args: "")
    monkeypatch.setattr(main.db, "add_log", lambda *_args: None)
    session = FakeSession(
        FakeResponse(
            200,
            payload={"choices": [{"message": {"content": "   "}}]},
        )
    )

    with pytest.raises(main.AIRewriteError, match="пустой ответ"):
        asyncio.run(main._ai_rewrite("original source text", ai_config(), session))


def test_failed_rewrite_leaves_post_unprocessed_for_retry(monkeypatch) -> None:
    logs: list[tuple] = []
    skipped: list[tuple] = []
    published: list[tuple] = []

    async def fail_rewrite(*_args, **_kwargs):
        raise main.AIRewriteError("AI endpoint недоступен")

    async def unexpected_media_download(*_args, **_kwargs):
        raise AssertionError("Media must not be downloaded when rewrite fails")

    monkeypatch.setattr(main, "_ai_rewrite", fail_rewrite)
    monkeypatch.setattr(main, "_download_media", unexpected_media_download)
    monkeypatch.setattr(main.db, "add_log", lambda *args: logs.append(args))
    monkeypatch.setattr(main.db, "save_skipped_post", lambda *args: skipped.append(args))
    monkeypatch.setattr(main.db, "save_post", lambda *args: published.append(args))

    message = SimpleNamespace(id=42, message="Original text", media=None)
    main.bot_running.set()
    try:
        asyncio.run(
            main._process_message_group(
                client=None,
                source="@source",
                messages=[message],
                cfg={
                    "ads_words": [],
                    "watermark": "",
                    "topic_channels": {"общее": "@target"},
                },
                bot=None,
                ai_session=None,
            )
        )
    finally:
        main.bot_running.clear()

    assert skipped == []
    assert published == []
    assert any(entry[0] == "WARN-405" for entry in logs)



def test_send_failure_does_not_permanently_skip_post(monkeypatch) -> None:
    logs: list[tuple] = []
    skipped: list[tuple] = []
    published: list[tuple] = []

    async def successful_rewrite(*_args, **_kwargs):
        return "общее", "Rewritten text"

    async def no_media(*_args, **_kwargs):
        return [], []

    class FailingBot:
        async def send_message(self, **_kwargs):
            raise RuntimeError("network connection reset")

    monkeypatch.setattr(main, "_ai_rewrite", successful_rewrite)
    monkeypatch.setattr(main, "_download_media", no_media)
    monkeypatch.setattr(main, "SLEEP_BETWEEN_POSTS_S", 0)
    monkeypatch.setattr(main.db, "add_log", lambda *args: logs.append(args))
    monkeypatch.setattr(main.db, "save_skipped_post", lambda *args: skipped.append(args))
    monkeypatch.setattr(main.db, "save_post", lambda *args: published.append(args))

    message = SimpleNamespace(id=43, message="Source text", media=None)
    main.bot_running.set()
    try:
        asyncio.run(
            main._process_message_group(
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
        )
    finally:
        main.bot_running.clear()

    assert skipped == []
    assert published == []
    assert any(entry[0] == "WARN-406" for entry in logs)
