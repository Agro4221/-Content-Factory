"""Validate that saved topic/channel settings always remain a JSON object."""
from __future__ import annotations

import asyncio
import json

import pytest
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


@pytest.mark.parametrize(
    "raw",
    ["not-json", "[]", "null", "42", "true", '"string"', '{"news": 42}', '{"news": null}'],
)
def test_invalid_topic_channels_are_rejected_without_writes(monkeypatch, raw: str) -> None:
    db = FakeDB()
    monkeypatch.setattr(Site, "db", db)

    result = _save({"topic_channels": raw})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert result.body
    assert db.writes == []


def test_topic_channels_object_is_trimmed_and_saved_as_valid_json(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(Site, "db", db)

    result = _save({"topic_channels": '{" news ": " @target ", "empty": "  "}'})

    assert result == {"status": "success", "saved": 1}
    assert json.loads(db.settings["topic_channels"]) == {"news": "@target"}

@pytest.mark.parametrize(
    ("raw_target", "normalized"),
    [
        ("https://t.me/news_channel", "@news_channel"),
        ("t.me/news_channel/123", "@news_channel"),
        ("https://t.me/s/news_channel/123", "@news_channel"),
        ("https://t.me/c/123456789/12", "-100123456789"),
        ("-100123456789", "-100123456789"),
        ("@news_channel", "@news_channel"),
    ],
)
def test_public_channel_links_are_normalized_for_bot_api(
    monkeypatch,
    raw_target: str,
    normalized: str,
) -> None:
    db = FakeDB()
    monkeypatch.setattr(Site, "db", db)

    result = _save({
        "topic_channels": json.dumps({"news": raw_target}, ensure_ascii=False)
    })

    assert result == {"status": "success", "saved": 1}
    assert json.loads(db.settings["topic_channels"]) == {"news": normalized}


@pytest.mark.parametrize(
    "invite_link",
    ["https://t.me/+invitehash", "t.me/joinchat/invitehash"],
)
def test_telegram_invite_link_is_rejected_as_publication_target(
    monkeypatch,
    invite_link: str,
) -> None:
    db = FakeDB()
    monkeypatch.setattr(Site, "db", db)

    result = _save({
        "topic_channels": json.dumps({"news": invite_link}, ensure_ascii=False)
    })

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert db.writes == []

@pytest.mark.parametrize(
    "invalid_link",
    [
        "https://t.me/share/url?url=https://example.com",
        "https://t.me/proxy?server=proxy.example&port=443&secret=00112233445566778899aabbccddeeff",
        "ftp://t.me/news_channel",
        "https://t.me/c/not-a-number/123",
    ],
)
def test_non_channel_telegram_links_are_rejected_as_targets(
    monkeypatch,
    invalid_link: str,
) -> None:
    db = FakeDB()
    monkeypatch.setattr(Site, "db", db)

    result = _save({
        "topic_channels": json.dumps({"news": invalid_link}, ensure_ascii=False)
    })

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert db.writes == []


def test_www_telegram_channel_link_is_normalized(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(Site, "db", db)

    result = _save({
        "topic_channels": json.dumps(
            {"news": "www.t.me/news_channel/123"}, ensure_ascii=False
        )
    })

    assert result == {"status": "success", "saved": 1}
    assert json.loads(db.settings["topic_channels"]) == {"news": "@news_channel"}

