"""Regression tests for publication-record database write failures."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import pytest

import main
from database import DB, DatabaseWriteError


def test_save_post_propagates_non_duplicate_sqlite_failures() -> None:
    db = object.__new__(DB)
    db._lock = threading.Lock()
    connection = sqlite3.connect(":memory:")
    connection.close()
    db.conn = connection

    with pytest.raises(DatabaseWriteError, match="could not record publication"):
        db.save_post("@source", 17)


def test_save_post_treats_an_existing_composite_key_as_duplicate() -> None:
    db = object.__new__(DB)
    db._lock = threading.Lock()
    db.conn = sqlite3.connect(":memory:")
    try:
        db.conn.execute(
            "CREATE TABLE published_posts (post_key TEXT PRIMARY KEY, post_id TEXT, source_name TEXT, created_at DATETIME)"
        )
        db.save_post("@source", 17)
        db.save_post("@source", 17)
        assert db.conn.execute("SELECT COUNT(*) FROM published_posts").fetchone()[0] == 1
    finally:
        db.conn.close()


def test_database_failure_after_send_is_not_mislabeled_as_send_failure(monkeypatch) -> None:
    logs: list[tuple[str, str]] = []
    sent: list[dict] = []
    skipped: list[tuple[str, int, str]] = []

    class FakeDB:
        def add_log(self, level: str, message: str) -> None:
            logs.append((level, message))

        def save_post(self, source: str, post_id: int) -> None:
            raise DatabaseWriteError("simulated disk I/O failure")

        def save_skipped_post(self, source: str, post_id: int, reason: str) -> None:
            skipped.append((source, post_id, reason))

    class Running:
        running = True

        def is_set(self) -> bool:
            return self.running

        def clear(self) -> None:
            self.running = False

    class FakeBot:
        async def send_message(self, **kwargs) -> None:
            sent.append(kwargs)

    async def fake_rewrite(*_args, **_kwargs) -> tuple[str, str]:
        return "news", "rewritten output"

    async def fake_download(*_args, **_kwargs):
        return [], []

    running = Running()
    monkeypatch.setattr(main, "db", FakeDB())
    monkeypatch.setattr(main, "bot_running", running)
    monkeypatch.setattr(main, "_ai_rewrite", fake_rewrite)
    monkeypatch.setattr(main, "_download_media", fake_download)
    monkeypatch.setattr(main, "SLEEP_BETWEEN_POSTS_S", 0)

    cfg = {
        "ads_words": [],
        "watermark": "",
        "topic_channels": {"news": "@target"},
    }
    message = SimpleNamespace(id=71, message="source caption", media=None)

    asyncio.run(
        main._process_message_group(None, "@source", [message], cfg, FakeBot(), None)
    )

    assert sent == [{"chat_id": "@target", "text": "rewritten output"}]
    assert running.is_set() is False
    assert skipped == []
    assert len(logs) == 1
    assert logs[0][0] == "ERR-DB-501"
    assert "Перед перезапуском проверьте целевой канал" in logs[0][1]

def test_save_posts_rolls_back_entire_group_when_one_insert_fails() -> None:
    db = object.__new__(DB)
    db._lock = threading.Lock()
    db.conn = sqlite3.connect(":memory:")
    try:
        db.conn.execute(
            "CREATE TABLE published_posts (post_key TEXT PRIMARY KEY, post_id TEXT, source_name TEXT, created_at DATETIME)"
        )
        db.conn.execute(
            """
            CREATE TRIGGER reject_second_group_item
            BEFORE INSERT ON published_posts
            WHEN NEW.post_id = '18'
            BEGIN
                SELECT RAISE(ABORT, 'simulated disk full');
            END;
            """
        )

        with pytest.raises(DatabaseWriteError, match="could not record publication group"):
            db.save_posts("@source", [17, 18, 19])

        assert db.conn.execute("SELECT COUNT(*) FROM published_posts").fetchone()[0] == 0
        assert not db.conn.in_transaction
    finally:
        db.conn.close()


def test_save_posts_is_idempotent_for_already_recorded_group_items() -> None:
    db = object.__new__(DB)
    db._lock = threading.Lock()
    db.conn = sqlite3.connect(":memory:")
    try:
        db.conn.execute(
            "CREATE TABLE published_posts (post_key TEXT PRIMARY KEY, post_id TEXT, source_name TEXT, created_at DATETIME)"
        )

        db.save_posts("@source", [17, 18])
        db.save_posts("@source", [17, 18])

        assert db.conn.execute("SELECT COUNT(*) FROM published_posts").fetchone()[0] == 2
    finally:
        db.conn.close()

def test_set_settings_rolls_back_entire_batch_when_one_write_fails() -> None:
    db = object.__new__(DB)
    db._lock = threading.Lock()
    db.conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        db.conn.execute(
            "CREATE TABLE bot_settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        db.conn.execute(
            "INSERT INTO bot_settings (key, value) VALUES ('existing', 'original')"
        )
        db.conn.execute(
            """
            CREATE TRIGGER reject_one_setting
            BEFORE INSERT ON bot_settings
            WHEN NEW.key = 'reject'
            BEGIN
                SELECT RAISE(ABORT, 'simulated disk full');
            END;
            """
        )

        with pytest.raises(RuntimeError, match="transaction rolled back"):
            db.set_settings({
                "existing": "updated",
                "reject": "value",
                "later": "value",
            })

        rows = dict(db.conn.execute("SELECT key, value FROM bot_settings"))
        assert rows == {"existing": "original"}
        assert not db.conn.in_transaction
    finally:
        db.conn.close()


def test_set_settings_commits_whole_batch() -> None:
    db = object.__new__(DB)
    db._lock = threading.Lock()
    db.conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        db.conn.execute(
            "CREATE TABLE bot_settings (key TEXT PRIMARY KEY, value TEXT)"
        )

        db.set_settings({"ai_mode": "LOCAL", "local_port": 1234})

        assert dict(db.conn.execute("SELECT key, value FROM bot_settings")) == {
            "ai_mode": "LOCAL",
            "local_port": "1234",
        }
    finally:
        db.conn.close()

def test_save_skipped_post_propagates_non_duplicate_integrity_failures() -> None:
    db = object.__new__(DB)
    db._lock = threading.Lock()
    db.conn = sqlite3.connect(":memory:")
    try:
        db.conn.execute(
            """
            CREATE TABLE skipped_posts (
                post_key TEXT PRIMARY KEY,
                post_id TEXT,
                source_name TEXT,
                reason TEXT CHECK (reason = 'allowed'),
                created_at DATETIME
            )
            """
        )
        with pytest.raises(DatabaseWriteError, match="could not record skipped post"):
            db.save_skipped_post("@source", 17, "invalid-reason")
    finally:
        db.conn.close()


def test_save_skipped_post_treats_existing_key_as_idempotent() -> None:
    db = object.__new__(DB)
    db._lock = threading.Lock()
    db.conn = sqlite3.connect(":memory:")
    try:
        db.conn.execute(
            """
            CREATE TABLE skipped_posts (
                post_key TEXT PRIMARY KEY,
                post_id TEXT,
                source_name TEXT,
                reason TEXT,
                created_at DATETIME
            )
            """
        )
        db.save_skipped_post("@source", 17, "empty")
        db.save_skipped_post("@source", 17, "empty")
        assert db.conn.execute("SELECT COUNT(*) FROM skipped_posts").fetchone()[0] == 1
    finally:
        db.conn.close()

