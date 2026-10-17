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
