"""Regression tests for proxy-check errors that must not be reported as success."""
from __future__ import annotations

import asyncio

import aiohttp

import proxy_check


def test_unsupported_proxy_type_is_not_tested_as_http(monkeypatch) -> None:
    async def unexpected_http(*_args, **_kwargs):
        raise AssertionError("unsupported proxy type must never be tested as HTTP")

    monkeypatch.setattr(proxy_check, "_test_http", unexpected_http)

    result = asyncio.run(
        proxy_check.check_proxy({
            "use_proxy": "1",
            "proxy_type": "socks7",
            "proxy_host": "proxy.example",
            "proxy_port": "8080",
        })
    )

    assert result["ok"] is False
    assert result["reachable"] is False
    assert "Неподдерживаемый тип прокси" in result["message"]


def test_http_proxy_407_is_reported_as_authentication_failure(monkeypatch) -> None:
    try:
        error = aiohttp.ClientHttpProxyError(
            request_info=None,
            history=(),
            status=407,
            message="Proxy Authentication Required",
        )
    except TypeError:
        # Compatibility for aiohttp versions exposing the older constructor.
        error = aiohttp.ClientHttpProxyError(
            request_info=None,
            history=(),
            code=407,
            message="Proxy Authentication Required",
        )

    class RequestContext:
        async def __aenter__(self):
            raise error

        async def __aexit__(self, *_args):
            return False

    class FakeSession:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return RequestContext()

    monkeypatch.setattr(proxy_check.aiohttp, "ClientSession", FakeSession)

    ok, message = asyncio.run(
        proxy_check._test_http(
            "proxy.example", 8080, "bad-user", "bad-password", timeout=1
        )
    )

    assert ok is False
    assert "авторизацию" in message.lower()
