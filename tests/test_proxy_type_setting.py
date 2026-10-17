"""Regression tests for validating proxy type settings."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse

import Site


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTP", "http"),
        ("http", "http"),
        ("SOCKS5", "socks5"),
        ("socks4", "socks4"),
        ("MTProto", "mtproto"),
        ("", ""),
    ],
)
def test_proxy_type_is_normalized(raw: str, expected: str) -> None:
    assert Site._normalize_validated_setting("proxy_type", raw) == expected


@pytest.mark.parametrize("raw", ["socks7", "http_proxy", "unknown"])
def test_unsupported_proxy_type_is_rejected(raw: str) -> None:
    with pytest.raises(Site.SettingsValidationError) as exc:
        Site._normalize_validated_setting("proxy_type", raw)

    assert exc.value.field == "proxy_type"


class FakeRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def json(self) -> dict:
        return self.payload


class FakeDB:
    def __init__(self) -> None:
        self.settings: dict[str, str] = {}

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value


def test_settings_endpoint_persists_canonical_proxy_type(monkeypatch) -> None:
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)

    result = asyncio.run(
        Site.api_post_settings(
            FakeRequest({"proxy_type": "SOCKS5"}),
            BackgroundTasks(),
        )
    )

    assert result == {"status": "success", "saved": 1}
    assert fake_db.settings == {"proxy_type": "socks5"}


def test_settings_endpoint_rejects_unsupported_proxy_type(monkeypatch) -> None:
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)

    result = asyncio.run(
        Site.api_post_settings(
            FakeRequest({"proxy_type": "SOCKS7"}),
            BackgroundTasks(),
        )
    )

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert fake_db.settings == {}
