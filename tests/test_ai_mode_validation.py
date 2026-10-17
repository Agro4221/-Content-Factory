"""Regression tests for AI mode settings validation."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse
from starlette.requests import Request

import Site


def make_json_request(payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")
    delivered = False

    async def receive() -> dict:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/settings",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        },
        receive,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("LOCAL", "LOCAL"),
        ("local", "LOCAL"),
        (" Local ", "LOCAL"),
        ("REMOTE", "REMOTE"),
        ("remote", "REMOTE"),
        (" Remote ", "REMOTE"),
    ],
)
def test_ai_mode_is_normalized_to_supported_values(raw: str, expected: str) -> None:
    assert Site._normalize_validated_setting("ai_mode", raw) == expected


@pytest.mark.parametrize("raw", ["", "CLOUD", "LOACL", "local/remote", None])
def test_unsupported_ai_mode_is_rejected(raw) -> None:
    with pytest.raises(Site.SettingsValidationError) as exc:
        Site._normalize_validated_setting("ai_mode", raw)

    assert exc.value.field == "ai_mode"


def test_settings_endpoint_returns_422_and_does_not_save_invalid_ai_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[tuple[str, str]] = []

    class FakeDB:
        def set_setting(self, key: str, value: str) -> None:
            saved.append((key, value))

    monkeypatch.setattr(Site, "db", FakeDB())

    response = asyncio.run(
        Site.api_post_settings(make_json_request({"ai_mode": "CLOUD"}), BackgroundTasks())
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 422
    assert json.loads(response.body)["field"] == "ai_mode"
    assert saved == []


def test_settings_endpoint_saves_canonical_ai_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[tuple[str, str]] = []

    class FakeDB:
        def set_setting(self, key: str, value: str) -> None:
            saved.append((key, value))

    monkeypatch.setattr(Site, "db", FakeDB())

    response = asyncio.run(
        Site.api_post_settings(make_json_request({"ai_mode": "remote"}), BackgroundTasks())
    )

    assert response == {"status": "success", "saved": 1}
    assert saved == [("ai_mode", "REMOTE")]
