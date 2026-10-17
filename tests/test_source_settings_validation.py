"""Validate channel sources before the parser attempts network resolution."""
from __future__ import annotations

import asyncio

from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse

import Site


class FakeRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def json(self) -> dict:
        return self.payload


class FakeDB:
    def __init__(self) -> None:
        self.settings: dict[str, str] = {}
        self.writes: list[tuple[str, str]] = []

    def get_all_settings(self) -> dict[str, str]:
        return dict(self.settings)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value
        self.writes.append((key, value))


def _save(payload: dict):
    return asyncio.run(
        Site.api_post_settings(FakeRequest(payload), BackgroundTasks())
    )


def test_source_settings_are_normalized_and_deduplicated(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(Site, "db", db)

    result = _save({
        "sources": "https://t.me/News_Channel/123\n@NEWS_CHANNEL\nnews_channel\n-100123456789"
    })

    assert result == {"status": "success", "saved": 1}
    assert db.settings["sources"] == "@news_channel\n-100123456789"


def test_source_invite_or_non_telegram_link_is_rejected_before_write(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(Site, "db", db)

    result = _save({"sources": "https://t.me/+privateinvite"})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert db.writes == []


def test_source_settings_can_be_cleared(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(Site, "db", db)

    result = _save({"sources": ""})

    assert result == {"status": "success", "saved": 1}
    assert db.settings["sources"] == ""
