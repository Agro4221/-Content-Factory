"""Regression tests for per-source Telegram polling cursors."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import main

CURSOR_KEY = "_internal_source_cursors_json"
SOURCE = "@sample"


class FakeDB:
    def __init__(self, cursor: int | None = None) -> None:
        self.settings = (
            {CURSOR_KEY: json.dumps({SOURCE: cursor})}
            if cursor is not None
            else {}
        )
        self.published: set[tuple[str, int]] = set()
        self.skipped: set[tuple[str, int]] = set()
        self.logs: list[tuple[str, str]] = []

    def add_log(self, log_type: str, message: str) -> None:
        self.logs.append((log_type, message))

    def get_setting(self, key: str, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key: str, value) -> None:
        self.settings[key] = str(value)

    def is_published(self, source: str, message_id: int) -> bool:
        return (source, message_id) in self.published

    def is_skipped(self, source: str, message_id: int) -> bool:
        return (source, message_id) in self.skipped

    def save_post(self, source: str, message_id: int) -> None:
        self.published.add((source, message_id))

    def save_skipped_post(self, source: str, message_id: int, _reason: str) -> None:
        self.skipped.add((source, message_id))

    def cursor(self) -> int | None:
        raw = self.settings.get(CURSOR_KEY)
        if not raw:
            return None
        values = json.loads(raw)
        return values.get(SOURCE)


class FakeClient:
    def __init__(self, messages: list) -> None:
        self.messages = messages
        self.calls: list[dict] = []

    def iter_messages(self, _source: str, **kwargs):
        self.calls.append(kwargs)

        async def generate():
            messages = sorted(self.messages, key=lambda m: m.id)
            if "min_id" in kwargs:
                messages = [m for m in messages if m.id > kwargs["min_id"]]
                # reverse=True requests chronological (oldest-first) order.
            elif "max_id" in kwargs:
                messages = [m for m in messages if m.id < kwargs["max_id"]]
                messages = list(reversed(messages))[:kwargs.get("limit", len(messages))]
            else:
                messages = list(reversed(messages))[:kwargs.get("limit", len(messages))]
            for message in messages:
                yield message

        return generate()


class RunningBot:
    @staticmethod
    def is_set() -> bool:
        return True


def message(message_id: int, grouped_id=None):
    return SimpleNamespace(id=message_id, grouped_id=grouped_id)


def install_fakes(monkeypatch, db: FakeDB, messages: list):
    client = FakeClient(messages)
    processed: list[int] = []

    async def fake_process(_client, source, group, _cfg, _bot, _session):
        for item in sorted(group, key=lambda m: m.id):
            processed.append(item.id)
            db.save_post(source, item.id)

    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", RunningBot())
    monkeypatch.setattr(main, "_process_message_group", fake_process)
    return client, processed


def test_processes_all_new_messages_after_cursor(monkeypatch) -> None:
    db = FakeDB(cursor=100)
    client, processed = install_fakes(
        monkeypatch, db, [message(n) for n in range(101, 116)]
    )

    asyncio.run(main._process_one_source(client, SOURCE, {}, None, None))

    assert processed == list(range(101, 116))
    assert client.calls == [{"min_id": 100, "reverse": True}]
    assert db.cursor() == 115


def test_first_scan_keeps_latest_ten_message_baseline(monkeypatch) -> None:
    db = FakeDB()
    client, processed = install_fakes(
        monkeypatch, db, [message(n) for n in range(1, 16)]
    )

    asyncio.run(main._process_one_source(client, SOURCE, {}, None, None))

    assert processed == list(range(6, 16))
    assert client.calls == [{"limit": 10}]
    assert db.cursor() == 15


def test_cursor_stops_before_message_that_needs_retry(monkeypatch) -> None:
    db = FakeDB(cursor=100)
    client = FakeClient([message(n) for n in range(101, 106)])
    processed: list[int] = []
    fail_id = 103

    async def fake_process(_client, source, group, _cfg, _bot, _session):
        for item in sorted(group, key=lambda m: m.id):
            processed.append(item.id)
            if item.id != fail_id:
                db.save_post(source, item.id)

    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", RunningBot())
    monkeypatch.setattr(main, "_process_message_group", fake_process)

    asyncio.run(main._process_one_source(client, SOURCE, {}, None, None))

    assert processed == [101, 102, 103]
    assert db.cursor() == 102

    fail_id = -1
    processed.clear()
    asyncio.run(main._process_one_source(client, SOURCE, {}, None, None))

    assert processed == [103, 104, 105]
    assert db.cursor() == 105


def test_cursor_does_not_jump_over_interleaved_album_message(monkeypatch) -> None:
    db = FakeDB(cursor=100)
    client, processed = install_fakes(
        monkeypatch,
        db,
        [message(101, grouped_id="album"), message(102), message(103, grouped_id="album")],
    )

    asyncio.run(main._process_one_source(client, SOURCE, {}, None, None))

    assert processed == [101, 103, 102]
    assert db.cursor() == 103

@pytest.mark.parametrize("raw", ["{broken", "[]", '{"@sample": -1}', '{"@sample": true}'])
def test_corrupt_cursor_does_not_reset_to_latest_ten_messages(monkeypatch, raw: str) -> None:
    db = FakeDB()
    db.settings[CURSOR_KEY] = raw
    client = FakeClient([message(n) for n in range(1, 16)])
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", RunningBot())

    asyncio.run(main._process_one_source(client, SOURCE, {}, None, None))

    assert client.calls == []
    assert db.settings[CURSOR_KEY] == raw
    assert db.logs and db.logs[-1][0] == "ERR-207"

def test_first_scan_fetches_the_older_half_of_a_cut_off_album(monkeypatch) -> None:
    db = FakeDB()
    album = [message(n, grouped_id="album") for n in range(100, 110)]
    later = [message(n) for n in range(110, 116)]
    client, processed = install_fakes(monkeypatch, db, album + later)

    asyncio.run(main._process_one_source(client, SOURCE, {}, None, None))

    assert processed == list(range(100, 116))
    assert client.calls == [
        {"limit": 10},
        {"max_id": 106, "limit": 100},
    ]
    assert db.cursor() == 115

def test_flood_wait_from_source_read_reaches_main_cooldown_handler(monkeypatch) -> None:
    class FakeFloodWaitError(Exception):
        seconds = 23

    db = FakeDB()
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", RunningBot())
    monkeypatch.setattr(main, "FloodWaitError", FakeFloodWaitError)

    class FloodClient:
        def iter_messages(self, _source, **_kwargs):
            async def generate():
                raise FakeFloodWaitError("wait 23 seconds")
                yield message(1)

            return generate()

    with pytest.raises(FakeFloodWaitError):
        asyncio.run(main._process_one_source(FloodClient(), SOURCE, {}, None, None))

    assert db.logs == []
    assert db.cursor() is None

def test_legacy_link_cursor_matches_normalized_username_source(monkeypatch) -> None:
    db = FakeDB()
    db.settings[CURSOR_KEY] = json.dumps({"https://t.me/Sample/123": 100})
    client, processed = install_fakes(
        monkeypatch, db, [message(n) for n in range(101, 106)]
    )

    asyncio.run(main._process_one_source(client, SOURCE, {}, None, None))

    assert client.calls == [{"min_id": 100, "reverse": True}]
    assert processed == [101, 102, 103, 104, 105]
    assert db.cursor() == 105


def test_cursor_alias_collision_uses_earliest_value_to_avoid_losing_backlog(monkeypatch) -> None:
    db = FakeDB()
    db.settings[CURSOR_KEY] = json.dumps({
        "@sample": 120,
        "https://t.me/sample/123": 100,
    })
    monkeypatch.setattr(main, "db", db)

    assert main._get_source_cursor(SOURCE) == 100

def test_revoked_session_stops_parser_instead_of_reconnecting_forever(monkeypatch) -> None:
    class FakeAuthKeyUnregisteredError(Exception):
        pass

    class ToggleRunningBot:
        def __init__(self) -> None:
            self.active = True

        def is_set(self) -> bool:
            return self.active

        def clear(self) -> None:
            self.active = False

    class AuthFailClient:
        def iter_messages(self, _source, **_kwargs):
            async def generate():
                raise FakeAuthKeyUnregisteredError("session revoked")
                yield message(101)

            return generate()

    db = FakeDB(cursor=100)
    running = ToggleRunningBot()
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", running)
    monkeypatch.setattr(main, "AuthKeyUnregisteredError", FakeAuthKeyUnregisteredError)

    result = asyncio.run(main._process_one_source(AuthFailClient(), SOURCE, {}, None, None))

    assert result is False
    assert running.is_set() is False
    assert db.logs and db.logs[-1][0] == "ERR-203"
    assert db.cursor() == 100
