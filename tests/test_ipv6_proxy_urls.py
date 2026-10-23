"""Regression tests for IPv6 literals in generated proxy URLs."""
from __future__ import annotations

import asyncio

import main
import proxy_check


def test_format_proxy_host_brackets_ipv6_only() -> None:
    assert proxy_check.format_proxy_host("2001:db8::1") == "[2001:db8::1]"
    assert proxy_check.format_proxy_host("[2001:db8::1]") == "[2001:db8::1]"
    assert proxy_check.format_proxy_host("192.0.2.10") == "192.0.2.10"
    assert proxy_check.format_proxy_host("proxy.example") == "proxy.example"


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.request_kwargs = None
        FakeSession.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, url, **kwargs):
        self.request_kwargs = {"url": url, **kwargs}
        return FakeResponse()


def test_http_proxy_check_brackets_ipv6_host(monkeypatch) -> None:
    FakeSession.instances.clear()
    monkeypatch.setattr(proxy_check.aiohttp, "ClientSession", FakeSession)

    ok, _ = asyncio.run(
        proxy_check._test_http("2001:db8::1", 8080, "", "", timeout=1)
    )

    assert ok is True
    assert FakeSession.instances[-1].request_kwargs["proxy"] == "http://[2001:db8::1]:8080"


class FakeConnector:
    urls = []

    @classmethod
    def from_url(cls, url):
        cls.urls.append(url)
        return cls()

    async def close(self):
        return None


def test_socks_proxy_check_brackets_ipv6_host(monkeypatch) -> None:
    FakeSession.instances.clear()
    FakeConnector.urls.clear()
    monkeypatch.setattr(proxy_check, "_HAS_SOCKS", True)
    monkeypatch.setattr(proxy_check, "ProxyConnector", FakeConnector, raising=False)
    monkeypatch.setattr(proxy_check.aiohttp, "ClientSession", FakeSession)

    ok, _ = asyncio.run(
        proxy_check._test_socks("socks5", "2001:db8::1", 1080, "", "", timeout=1)
    )

    assert ok is True
    assert FakeConnector.urls == ["socks5://[2001:db8::1]:1080"]



class FakeSenderSession:
    instances = []

    def __init__(self, *, proxy: str) -> None:
        self.proxy = proxy
        FakeSenderSession.instances.append(self)


class FakeSenderDB:
    def __init__(self) -> None:
        self.logs = []

    def add_log(self, log_type: str, message: str) -> None:
        self.logs.append((log_type, message))


def test_http_sender_brackets_ipv6_and_keeps_telethon_host_unbracketed(monkeypatch) -> None:
    FakeSenderSession.instances.clear()
    monkeypatch.setattr(main, "AiohttpSession", FakeSenderSession)
    monkeypatch.setattr(main, "db", FakeSenderDB())

    cfg = {
        "use_proxy": True,
        "proxy_type": "http",
        "proxy_host": "2001:db8::1",
        "proxy_port": "8080",
        "proxy_user": "name@domain",
        "proxy_pass": "p@ss:/?#%",
        "mtproto_key": "",
    }

    setup = main._setup_proxy(cfg)

    assert setup.mtproto_kwargs is None
    assert setup.telethon_proxy["addr"] == "2001:db8::1"
    assert setup.bot_session is FakeSenderSession.instances[-1]
    assert setup.bot_session.proxy == (
        "http://name%40domain:p%40ss%3A%2F%3F%23%25@[2001:db8::1]:8080"
    )


def test_socks_sender_brackets_ipv6_and_encodes_credentials(monkeypatch) -> None:
    FakeSenderSession.instances.clear()
    monkeypatch.setattr(main, "AiohttpSession", FakeSenderSession)
    monkeypatch.setattr(main, "_HAS_SOCKS", True)
    monkeypatch.setattr(main, "db", FakeSenderDB())

    cfg = {
        "proxy_type": "socks5",
        "proxy_host": "2001:db8::1",
        "proxy_port": "1080",
        "proxy_user": "name@domain",
        "proxy_pass": "p@ss:/?#%",
    }

    session = main._make_socks_session(cfg)

    assert session is FakeSenderSession.instances[-1]
    assert session.proxy == (
        "socks5://name%40domain:p%40ss%3A%2F%3F%23%25@[2001:db8::1]:1080"
    )
