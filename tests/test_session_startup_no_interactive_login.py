"""The parser must never block on an interactive Telethon login prompt."""
from __future__ import annotations

import asyncio

import main


class FakeDB:
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []

    def add_log(self, level: str, message: str) -> None:
        self.logs.append((level, message))


class FakeClient:
    def __init__(self, authorized: bool) -> None:
        self.authorized = authorized
        self.connected = False
        self.start_called = False

    async def connect(self) -> None:
        self.connected = True

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def start(self) -> None:
        self.start_called = True
        raise AssertionError("interactive login must not be attempted")


def test_authorized_session_connects_without_starting_interactive_login(monkeypatch) -> None:
    db = FakeDB()
    client = FakeClient(authorized=True)
    monkeypatch.setattr(main, "db", db)

    result = asyncio.run(main._connect_authorized_client(client))

    assert result is True
    assert client.connected is True
    assert client.start_called is False
    assert db.logs == []


def test_unauthorized_session_returns_false_and_logs_actionable_error(monkeypatch) -> None:
    db = FakeDB()
    client = FakeClient(authorized=False)
    monkeypatch.setattr(main, "db", db)

    result = asyncio.run(main._connect_authorized_client(client))

    assert result is False
    assert client.connected is True
    assert client.start_called is False
    assert len(db.logs) == 1
    assert db.logs[0][0] == "ERR-203"
    assert "не авторизована" in db.logs[0][1].lower()
