"""Missing topic routes are configuration errors, not permanent post skips."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import main


class FakeDB:
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []
        self.published: list[tuple[str, int]] = []
        self.skipped: list[tuple[str, int, str]] = []

    def add_log(self, level: str, message: str) -> None:
        self.logs.append((level, message))

    def save_post(self, source: str, post_id: int) -> None:
        self.published.append((source, post_id))

    def save_skipped_post(self, source: str, post_id: int, reason: str) -> None:
        self.skipped.append((source, post_id, reason))


class Running:
    @staticmethod
    def is_set() -> bool:
        return True


class FakeBot:
    async def send_message(self, **_kwargs) -> None:
        raise AssertionError("post must remain pending without a configured target")


def test_missing_topic_route_leaves_source_post_retryable(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", Running())
    monkeypatch.setattr(main, "SLEEP_BETWEEN_POSTS_S", 0)

    async def rewrite(*_args, **_kwargs):
        return "sports", "Rewritten text"

    monkeypatch.setattr(main, "_ai_rewrite", rewrite)
    message = SimpleNamespace(id=101, message="Source text", media=None)
    cfg = {
        "ads_words": [],
        "watermark": "",
        "topic_channels": {"news": "@target"},
    }

    asyncio.run(
        main._process_message_group(
            None, "@source", [message], cfg, FakeBot(), None
        )
    )

    assert db.published == []
    assert db.skipped == []
    assert len(db.logs) == 1
    assert db.logs[0][0] == "WARN-400"
    assert "оставлен для повтора" in db.logs[0][1]
