"""Regression tests for HTTP proxy URLs with reserved characters in credentials."""
from __future__ import annotations

from yarl import URL

import main


def test_http_proxy_credentials_are_percent_encoded(
    monkeypatch,
) -> None:
    proxy_urls: list[str] = []

    class FakeAiohttpSession:
        def __init__(self, *, proxy: str) -> None:
            proxy_urls.append(proxy)

    monkeypatch.setattr(main, "AiohttpSession", FakeAiohttpSession)

    cfg = {
        "use_proxy": True,
        "proxy_type": "http",
        "proxy_host": "proxy.example",
        "proxy_port": "3128",
        "proxy_user": "name@domain",
        "proxy_pass": "p@ss:/?#%",
        "mtproto_key": "",
    }

    setup = main._setup_proxy(cfg)

    assert setup.bot_session is not None
    assert proxy_urls == [
        "http://name%40domain:p%40ss%3A%2F%3F%23%25@proxy.example:3128"
    ]

    parsed = URL(proxy_urls[0])
    assert parsed.user == cfg["proxy_user"]
    assert parsed.password == cfg["proxy_pass"]
    assert parsed.host == cfg["proxy_host"]
    assert parsed.port == int(cfg["proxy_port"])

def test_http_proxy_does_not_drop_single_credential_field(monkeypatch) -> None:
    urls: list[str] = []

    class FakeAiohttpSession:
        def __init__(self, *, proxy: str) -> None:
            urls.append(proxy)

    monkeypatch.setattr(main, "AiohttpSession", FakeAiohttpSession)

    for user, password in [("alice", ""), ("", "secret")]:
        main._setup_proxy({
            "use_proxy": True,
            "proxy_type": "http",
            "proxy_host": "proxy.example",
            "proxy_port": "3128",
            "proxy_user": user,
            "proxy_pass": password,
            "mtproto_key": "",
        })

    assert urls == [
        "http://alice:@proxy.example:3128",
        "http://:secret@proxy.example:3128",
    ]

