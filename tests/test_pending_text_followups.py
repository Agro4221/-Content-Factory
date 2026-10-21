"""Durable queue tests for Telegram text tails separated from media."""
from __future__ import annotations

import asyncio

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage

import main
from database import CURRENT_SCHEMA_VERSION, DB, DatabaseWriteError


def _new_db(tmp_path) -> DB:
    return DB(str(tmp_path / "followups.sqlite3"))


def test_followup_schema_is_migrated_and_queue_is_durable(tmp_path) -> None:
    db = _new_db(tmp_path)
    try:
        version = db.conn.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row[1]
            for row in db.conn.execute("PRAGMA table_info(pending_text_followups)")
        }
        assert version == CURRENT_SCHEMA_VERSION
        assert version >= 7
        assert {
            "id", "source_name", "post_id", "target_chat", "text",
            "attempts", "last_error", "next_attempt_at",
        } <= columns

        db.save_posts_with_followup("@source", [17, 18], "@target", "the undelivered tail")

        assert db.is_published("@source", 17)
        assert db.is_published("@source", 18)
        due = db.get_due_pending_text_followups()
        assert len(due) == 1
        assert due[0]["source_name"] == "@source"
        assert due[0]["post_id"] == "17"
        assert due[0]["target_chat"] == "@target"
        assert due[0]["text"] == "the undelivered tail"

        # Replaying the same transaction cannot duplicate or overwrite the tail.
        db.save_posts_with_followup("@source", [17, 18], "@target", "different tail")
        assert len(db.get_due_pending_text_followups()) == 1
        assert db.get_due_pending_text_followups()[0]["text"] == "the undelivered tail"
    finally:
        db.conn.close()


def test_existing_v6_database_migrates_the_followup_queue(tmp_path) -> None:
    path = tmp_path / "upgrade-v6.sqlite3"
    db = DB(str(path))
    db.conn.execute("DROP TABLE pending_text_followups")
    db.conn.execute("PRAGMA user_version = 6")
    db.conn.close()

    upgraded = DB(str(path))
    try:
        version = upgraded.conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in upgraded.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        assert version == CURRENT_SCHEMA_VERSION
        assert "pending_text_followups" in tables
        assert "post_queue" in tables
        assert upgraded.get_due_pending_text_followups() == []
    finally:
        upgraded.conn.close()


def test_followup_and_published_markers_roll_back_atomically(tmp_path) -> None:
    db = _new_db(tmp_path)
    try:
        db.conn.execute(
            """
            CREATE TRIGGER reject_followup BEFORE INSERT ON pending_text_followups
            BEGIN SELECT RAISE(ABORT, 'simulated queue failure'); END;
            """
        )

        with pytest.raises(DatabaseWriteError, match="atomically record publication"):
            db.save_posts_with_followup("@source", [31, 32], "@target", "tail")

        assert not db.is_published("@source", 31)
        assert not db.is_published("@source", 32)
        assert db.conn.execute("SELECT COUNT(*) FROM published_posts").fetchone()[0] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM pending_text_followups").fetchone()[0] == 0
    finally:
        db.conn.close()


def test_failed_followup_uses_retry_backoff_and_keeps_text(tmp_path) -> None:
    db = _new_db(tmp_path)
    try:
        db.save_posts_with_followup("@source", [41], "@target", "tail")
        followup = db.get_due_pending_text_followups()[0]

        db.record_pending_text_followup_failure(
            followup["id"], "TelegramRetryAfter: slow down", retry_after_seconds=120
        )

        assert db.get_due_pending_text_followups() == []
        row = db.conn.execute(
            "SELECT attempts, last_error, next_attempt_at FROM pending_text_followups WHERE id = ?",
            (followup["id"],),
        ).fetchone()
        assert row[0] == 1
        assert "TelegramRetryAfter" in row[1]
        assert row[2] > db.sqlite_datetime()
        assert db.conn.execute(
            "SELECT text FROM pending_text_followups WHERE id = ?", (followup["id"],)
        ).fetchone()[0] == "tail"
    finally:
        db.conn.close()


def test_successful_followup_acknowledgement_removes_only_queue_item(tmp_path) -> None:
    db = _new_db(tmp_path)
    try:
        db.save_posts_with_followup("@source", [51], "@target", "tail")
        followup = db.get_due_pending_text_followups()[0]

        db.mark_pending_text_followup_sent(followup["id"])

        assert db.get_due_pending_text_followups() == []
        assert db.is_published("@source", 51)
    finally:
        db.conn.close()


def test_worker_sends_and_acknowledges_pending_tail(monkeypatch) -> None:
    pending = [{
        "id": 9,
        "source_name": "@source",
        "post_id": "90",
        "target_chat": "@target",
        "text": "tail to retry",
        "attempts": 0,
    }]
    calls: list[dict] = []
    acknowledgements: list[int] = []
    logs: list[tuple[str, str]] = []

    class FakeDB:
        def get_due_pending_text_followups(self, _limit: int):
            return list(pending)

        def mark_pending_text_followup_sent(self, followup_id: int) -> None:
            acknowledgements.append(followup_id)
            pending.clear()

        def record_pending_text_followup_failure(self, *_args, **_kwargs) -> None:
            raise AssertionError("success must not schedule a retry")

        def add_log(self, kind: str, message: str) -> None:
            logs.append((kind, message))

    class FakeBot:
        async def send_message(self, **kwargs) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(main, "db", FakeDB())
    main.bot_running.set()
    try:
        asyncio.run(main._retry_pending_text_followups(FakeBot()))
    finally:
        main.bot_running.clear()

    assert calls == [{"chat_id": "@target", "text": "tail to retry"}]
    assert acknowledgements == [9]
    assert pending == []
    assert any(kind == "OK-502" for kind, _ in logs)


def test_worker_reschedules_failed_tail_without_stopping_parser(monkeypatch) -> None:
    pending = [{
        "id": 10,
        "source_name": "@source",
        "post_id": "91",
        "target_chat": "@target",
        "text": "tail to retry",
        "attempts": 0,
    }]
    failures: list[tuple] = []
    logs: list[tuple[str, str]] = []
    error = TelegramRetryAfter(
        method=SendMessage(chat_id="@target", text="tail to retry"),
        message="slow down",
        retry_after=60,
    )

    class FakeDB:
        def get_due_pending_text_followups(self, _limit: int):
            return list(pending)

        def record_pending_text_followup_failure(self, *args, **kwargs) -> None:
            failures.append((*args, kwargs))

        def mark_pending_text_followup_sent(self, *_args) -> None:
            raise AssertionError("failed delivery must stay queued")

        def add_log(self, kind: str, message: str) -> None:
            logs.append((kind, message))

    class FakeBot:
        async def send_message(self, **_kwargs) -> None:
            raise error

    monkeypatch.setattr(main, "db", FakeDB())
    main.bot_running.set()
    try:
        asyncio.run(main._retry_pending_text_followups(FakeBot()))
        assert main.bot_running.is_set()
    finally:
        main.bot_running.clear()

    assert len(failures) == 1
    assert failures[0][0] == 10
    assert failures[0][2]["retry_after_seconds"] == 60
    assert any(kind == "WARN-407" for kind, _ in logs)
