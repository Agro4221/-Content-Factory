"""Regression tests for consistent proxy toggle interpretation."""
from __future__ import annotations

import asyncio

import pytest

import Site


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", "1"),
        ("true", "1"),
        ("TRUE", "1"),
        ("yes", "1"),
        ("on", "1"),
        ("0", "0"),
        ("false", "0"),
        ("NO", "0"),
        ("off", "0"),
    ],
)
def test_proxy_toggle_values_are_normalized(raw: str, expected: str) -> None:
    assert Site._normalize_validated_setting("use_proxy", raw) == expected


def test_invalid_proxy_toggle_value_is_rejected() -> None:
    with pytest.raises(Site.SettingsValidationError) as exc:
        Site._normalize_validated_setting("use_proxy", "maybe")

    assert exc.value.field == "use_proxy"


def test_settings_endpoint_returns_canonical_proxy_toggle(monkeypatch) -> None:
    class FakeDB:
        def get_all_settings(self) -> dict[str, str]:
            return {"use_proxy": "yes", "proxy_host": "proxy.example"}

    monkeypatch.setattr(Site, "db", FakeDB())

    settings = asyncio.run(Site.api_get_settings(module=None))

    assert settings["use_proxy"] == "1"


def test_proxy_status_uses_same_truthy_semantics(monkeypatch) -> None:
    monkeypatch.setattr(
        Site,
        "_proxy_runtime_status",
        {
            "state": "unknown",
            "reachable": False,
            "proxy_type": "",
            "message": "",
            "checked_at": None,
        },
    )

    status = asyncio.run(
        Site.refresh_proxy_runtime_status(
            {"use_proxy": "On", "proxy_host": "", "proxy_type": "HTTP"}
        )
    )

    assert status["state"] == "no_host"
