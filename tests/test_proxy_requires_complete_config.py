"""Regression tests preventing enabled proxy settings from failing open."""
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
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.settings = dict(initial or {})
        self.writes: list[tuple[str, str]] = []

    def get_all_settings(self) -> dict[str, str]:
        return dict(self.settings)

    def set_setting(self, key: str, value: str) -> None:
        self.writes.append((key, value))
        self.settings[key] = value


def call_settings(payload: dict) -> dict | JSONResponse:
    return asyncio.run(
        Site.api_post_settings(
            FakeRequest(payload),
            BackgroundTasks(),
        )
    )


def test_cannot_enable_proxy_without_host(monkeypatch) -> None:
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)

    result = call_settings({
        "use_proxy": "1",
        "proxy_type": "HTTP",
        "proxy_host": "",
        "proxy_port": "",
    })

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert fake_db.writes == []
    assert "use_proxy" not in fake_db.settings


def test_partial_update_cannot_clear_host_while_proxy_remains_enabled(monkeypatch) -> None:
    fake_db = FakeDB({
        "use_proxy": "1",
        "proxy_type": "HTTP",
        "proxy_host": "proxy.example",
        "proxy_port": "8080",
    })
    monkeypatch.setattr(Site, "db", fake_db)

    result = call_settings({"proxy_host": ""})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert fake_db.settings["proxy_host"] == "proxy.example"
    assert fake_db.writes == []


def test_cannot_enable_proxy_without_valid_port(monkeypatch) -> None:
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)

    result = call_settings({
        "use_proxy": "1",
        "proxy_type": "HTTP",
        "proxy_host": "proxy.example",
        "proxy_port": "",
    })

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert fake_db.writes == []


def test_partial_update_rejects_existing_invalid_port_while_proxy_enabled(monkeypatch) -> None:
    fake_db = FakeDB({
        "use_proxy": "1",
        "proxy_type": "HTTP",
        "proxy_host": "proxy.example",
        "proxy_port": "not-a-port",
        "proxy_user": "",
    })
    monkeypatch.setattr(Site, "db", fake_db)

    result = call_settings({"proxy_user": "alice"})

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert fake_db.settings["proxy_port"] == "not-a-port"
    assert fake_db.settings["proxy_user"] == ""
    assert fake_db.writes == []


def test_disabling_proxy_can_clear_host_and_port(monkeypatch) -> None:
    fake_db = FakeDB({
        "use_proxy": "1",
        "proxy_host": "proxy.example",
        "proxy_port": "8080",
    })
    monkeypatch.setattr(Site, "db", fake_db)

    result = call_settings({
        "use_proxy": "0",
        "proxy_host": "",
        "proxy_port": "",
    })

    assert result == {"status": "success", "saved": 3}
    assert fake_db.settings["use_proxy"] == "0"
    assert fake_db.settings["proxy_host"] == ""
    assert fake_db.settings["proxy_port"] == ""


def test_valid_enabled_proxy_settings_are_saved(monkeypatch) -> None:
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)

    result = call_settings({
        "use_proxy": "1",
        "proxy_type": "HTTP",
        "proxy_host": "proxy.example",
        "proxy_port": "8080",
    })

    assert result == {"status": "success", "saved": 4}
    assert fake_db.settings["use_proxy"] == "1"
    assert fake_db.settings["proxy_host"] == "proxy.example"
    assert fake_db.settings["proxy_port"] == "8080"

def test_enabled_mtproto_requires_secret_before_settings_are_written(monkeypatch) -> None:
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)

    result = call_settings({
        "use_proxy": "1",
        "proxy_type": "mtproto",
        "proxy_host": "proxy.example",
        "proxy_port": "443",
        "mtproto_key": "",
    })

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert fake_db.writes == []


def test_enabled_mtproto_rejects_malformed_secret_before_settings_are_written(monkeypatch) -> None:
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)

    result = call_settings({
        "use_proxy": "1",
        "proxy_type": "mtproto",
        "proxy_host": "proxy.example",
        "proxy_port": "443",
        "mtproto_key": "bad-secret",
    })

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert fake_db.writes == []
    assert b'"field":"mtproto_key"' in result.body

def test_mtproto_link_is_normalized_and_saved_as_host_port_secret(monkeypatch) -> None:
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)
    secret = "00112233445566778899aabbccddeeff"

    result = call_settings({
        "use_proxy": "1",
        "proxy_type": "mtproto",
        "proxy_host": f"https://t.me/proxy?server=proxy.example&port=443&secret={secret}",
        "proxy_port": "",
        "mtproto_key": "",
    })

    assert result == {"status": "success", "saved": 5}
    assert fake_db.settings["proxy_host"] == "proxy.example"
    assert fake_db.settings["proxy_port"] == "443"
    assert fake_db.settings["mtproto_key"] == secret
    assert fake_db.settings["proxy_type"] == "mtproto"


def test_malformed_mtproto_link_is_rejected_without_saving(monkeypatch) -> None:
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)

    result = call_settings({
        "use_proxy": "1",
        "proxy_type": "mtproto",
        "proxy_host": "https://t.me/proxy?server=proxy.example&port=bad&secret=00112233445566778899aabbccddeeff",
        "proxy_port": "",
        "mtproto_key": "",
    })

    assert isinstance(result, JSONResponse)
    assert result.status_code == 422
    assert fake_db.writes == []

