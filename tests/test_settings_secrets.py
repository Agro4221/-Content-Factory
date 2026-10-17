"""Regression tests for redacting secrets in the settings API."""
from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks

import Site


class FakeDB:
    def __init__(self, settings: dict[str, str]) -> None:
        self.settings = dict(settings)
        self.writes: list[tuple[str, str]] = []

    def get_all_settings(self) -> dict[str, str]:
        return dict(self.settings)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value
        self.writes.append((key, value))


class FakeRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def json(self) -> dict:
        return self.payload


def test_get_settings_redacts_secret_values_but_keeps_regular_settings(monkeypatch) -> None:
    secrets = {
        "tg_api_hash": "telegram-hash-secret",
        "bot_token": "bot-token-secret",
        "remote_api_key": "remote-api-secret",
        "proxy_pass": "proxy-password-secret",
        "mtproto_key": "mtproto-secret",
        "license_key": "license-secret",
    }
    monkeypatch.setattr(Site, "db", FakeDB({**secrets, "poll_delay": "60"}))

    result = asyncio.run(Site.api_get_settings())

    for key in secrets:
        assert result[key] == Site._SECRET_SETTING_REDACTED_VALUE
        assert secrets[key] not in repr(result)
    assert result["poll_delay"] == "60"


def test_post_preserves_existing_secret_when_redaction_marker_is_submitted(monkeypatch) -> None:
    fake_db = FakeDB({"bot_token": "stored-token-secret", "poll_delay": "60"})
    monkeypatch.setattr(Site, "db", fake_db)
    payload = {
        "bot_token": Site._SECRET_SETTING_REDACTED_VALUE,
        "poll_delay": "90",
    }

    result = asyncio.run(
        Site.api_post_settings(FakeRequest(payload), BackgroundTasks())
    )

    assert result == {"status": "success", "saved": 1}
    assert fake_db.settings["bot_token"] == "stored-token-secret"
    assert fake_db.settings["poll_delay"] == "90"
    assert ("bot_token", Site._SECRET_SETTING_REDACTED_VALUE) not in fake_db.writes


def test_post_allows_explicit_secret_replacement_or_clearing(monkeypatch) -> None:
    fake_db = FakeDB({"bot_token": "old-token"})
    monkeypatch.setattr(Site, "db", fake_db)

    result = asyncio.run(
        Site.api_post_settings(FakeRequest({"bot_token": "new-token"}), BackgroundTasks())
    )
    assert result == {"status": "success", "saved": 1}
    assert fake_db.settings["bot_token"] == "new-token"

    result = asyncio.run(
        Site.api_post_settings(FakeRequest({"bot_token": ""}), BackgroundTasks())
    )
    assert result == {"status": "success", "saved": 1}
    assert fake_db.settings["bot_token"] == ""
