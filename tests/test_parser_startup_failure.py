"""The parser must clear its running flag when startup initialization fails."""
from __future__ import annotations

import asyncio

import main


class FakeDB:
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []

    def add_log(self, level: str, message: str) -> None:
        self.logs.append((level, message))


class Running:
    def __init__(self) -> None:
        self.running = True

    def is_set(self) -> bool:
        return self.running

    def clear(self) -> None:
        self.running = False


def test_parser_clears_running_state_when_temp_directory_cannot_be_created(monkeypatch) -> None:
    db = FakeDB()
    running = Running()

    def fail_makedirs(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", running)
    monkeypatch.setattr(main.os, "makedirs", fail_makedirs)

    asyncio.run(main.main())

    assert running.is_set() is False
    assert len(db.logs) == 1
    assert db.logs[0][0] == "ERR-100"
    assert "permission denied" in db.logs[0][1]


def test_parser_clears_running_state_when_startup_settings_check_fails(monkeypatch) -> None:
    db = FakeDB()
    running = Running()

    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", running)
    monkeypatch.setattr(main.os, "makedirs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.os.path, "exists", lambda *_args, **_kwargs: False)

    def fail_defaults():
        raise RuntimeError("settings database unavailable")

    monkeypatch.setattr(main, "warn_if_defaults", fail_defaults)

    asyncio.run(main.main())

    assert running.is_set() is False
    assert len(db.logs) == 1
    assert db.logs[0][0] == "ERR-100"
    assert "settings database unavailable" in db.logs[0][1]
