"""Protect runtime configuration from unsafe or malformed saved values."""
from __future__ import annotations

import pytest

import main


class FakeDB:
    def __init__(self, **settings) -> None:
        self.settings = settings

    def get_setting(self, key: str, default=None):
        return self.settings.get(key, default)


def test_invalid_persisted_ai_mode_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(main, "db", FakeDB(ai_mode="REMTOE"))

    with pytest.raises(ValueError, match="ai_mode"):
        main._load_config()


@pytest.mark.parametrize("port", ["", "abc", "0", "65536", "-1", "１２３４"])
def test_invalid_local_ai_port_is_rejected(monkeypatch, port: str) -> None:
    monkeypatch.setattr(main, "db", FakeDB(local_port=port))

    with pytest.raises(ValueError, match="local_port"):
        main._load_config()


def test_ipv6_local_ai_host_builds_valid_bracketed_url(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "db",
        FakeDB(local_host="::1", local_port="1234", ai_mode="LOCAL"),
    )

    cfg = main._load_config()

    assert cfg["local_url"] == "http://[::1]:1234/v1/chat/completions"


def test_empty_local_ai_host_uses_safe_default(monkeypatch) -> None:
    monkeypatch.setattr(main, "db", FakeDB(local_host="", ai_mode="LOCAL"))

    cfg = main._load_config()

    assert cfg["local_url"] == "http://127.0.0.1:1234/v1/chat/completions"
