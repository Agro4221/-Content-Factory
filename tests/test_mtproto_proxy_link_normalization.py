"""Normalize MTProto proxy invite URLs into the settings consumed by Telethon."""
from __future__ import annotations

import asyncio

import main
import proxy_check
import Site


SECRET = "00112233445566778899aabbccddeeff"


def test_https_tme_proxy_link_extracts_host_port_and_secret() -> None:
    result = proxy_check.normalize_mtproto_proxy_link({
        "use_proxy": "1",
        "proxy_type": "mtproto",
        "proxy_host": f"https://t.me/proxy?server=proxy.example&port=443&secret={SECRET}",
        "proxy_port": "",
        "mtproto_key": "",
    })

    assert result["proxy_type"] == "mtproto"
    assert result["proxy_host"] == "proxy.example"
    assert result["proxy_port"] == "443"
    assert result["mtproto_key"] == SECRET


def test_tg_deep_link_infers_mtproto_when_type_is_empty() -> None:
    result = proxy_check.normalize_mtproto_proxy_link({
        "proxy_type": "",
        "proxy_host": f"tg://proxy?server=proxy.example&port=8888&secret={SECRET}",
        "proxy_port": "",
        "mtproto_key": "",
    })

    assert result["proxy_type"] == "mtproto"
    assert result["proxy_host"] == "proxy.example"
    assert result["proxy_port"] == "8888"
    assert result["mtproto_key"] == SECRET


def test_explicit_http_type_does_not_get_overridden_by_mtproto_link() -> None:
    cfg = {
        "proxy_type": "http",
        "proxy_host": f"https://t.me/proxy?server=proxy.example&port=443&secret={SECRET}",
    }

    assert proxy_check.normalize_mtproto_proxy_link(cfg) == cfg


def test_invalid_mtproto_link_is_rejected() -> None:
    cfg = {
        "proxy_type": "mtproto",
        "proxy_host": "https://t.me/proxy?server=proxy.example&port=99999&secret=bad",
    }

    try:
        proxy_check.normalize_mtproto_proxy_link(cfg)
    except ValueError as exc:
        assert "port" in str(exc)
    else:
        raise AssertionError("invalid MTProto port must be rejected")


def test_api_proxy_test_normalizes_mtproto_link_before_checking(monkeypatch) -> None:
    captured: list[dict] = []

    class FakeRequest:
        async def json(self) -> dict:
            return {
                "use_proxy": "1",
                "proxy_type": "mtproto",
                "proxy_host": f"https://t.me/proxy?server=proxy.example&port=443&secret={SECRET}",
                "proxy_port": "",
                "mtproto_key": "",
            }

    async def fake_check(cfg: dict) -> dict:
        captured.append(dict(cfg))
        return {
            "ok": True,
            "reachable": True,
            "state": "active",
            "message": "ok",
            "proxy_type": "MTProto",
        }

    monkeypatch.setattr(Site, "check_proxy", fake_check)
    result = asyncio.run(Site.api_proxy_test(FakeRequest()))

    assert result["ok"] is True
    assert captured[0]["proxy_host"] == "proxy.example"
    assert captured[0]["proxy_port"] == "443"
    assert captured[0]["mtproto_key"] == SECRET


def test_runtime_setup_normalizes_mtproto_url_before_validating_port(monkeypatch) -> None:
    class FakeDB:
        def add_log(self, *_args) -> None:
            pass

    captured: list[tuple[str, int, str]] = []

    def fake_build(host: str, port: int, secret: str) -> dict:
        captured.append((host, port, secret))
        return {"connection": object(), "proxy": (host, port, secret)}

    monkeypatch.setattr(main, "db", FakeDB())
    monkeypatch.setattr(main, "build_mtproto_client_kwargs", fake_build)
    cfg = {
        "use_proxy": True,
        "proxy_type": "mtproto",
        "proxy_host": f"https://t.me/proxy?server=proxy.example&port=443&secret={SECRET}",
        "proxy_port": "",
        "mtproto_key": "",
    }

    telethon_proxy, mtproto_kwargs, bot_session = main._setup_proxy(cfg)

    assert telethon_proxy is None
    assert mtproto_kwargs is not None
    assert bot_session is None
    assert captured == [("proxy.example", 443, SECRET)]

def test_proxy_checker_normalizes_legacy_saved_mtproto_link(monkeypatch) -> None:
    captured: list[tuple[str, int, str]] = []

    async def fake_test_mtproto(host: str, port: int, secret: str, timeout: float):
        captured.append((host, port, secret))
        return True, "Соединение установлено"

    monkeypatch.setattr(proxy_check, "_test_mtproto", fake_test_mtproto)
    result = asyncio.run(proxy_check.check_proxy({
        "use_proxy": "1",
        "proxy_type": "mtproto",
        "proxy_host": f"https://t.me/proxy?server=proxy.example&port=443&secret={SECRET}",
        "proxy_port": "",
        "mtproto_key": "",
    }))

    assert result["ok"] is True
    assert captured == [("proxy.example", 443, SECRET)]

