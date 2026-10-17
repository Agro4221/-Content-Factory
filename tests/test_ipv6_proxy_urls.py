"""Regression tests for IPv6 literals in generated proxy URLs."""
from __future__ import annotations

import asyncio

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
