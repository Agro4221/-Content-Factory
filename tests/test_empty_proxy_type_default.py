"""Regression tests for a consistent proxy type fallback."""
from __future__ import annotations

import main
import proxy_check


class FakeAiohttpSession:
    def __init__(self, *, proxy: str) -> None:
        self.proxy = proxy


class FakeDB:
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []

    def add_log(self, log_type: str, message: str) -> None:
        self.logs.append((log_type, message))


def test_empty_proxy_type_uses_http_for_sender_and_checker(monkeypatch) -> None:
    monkeypatch.setattr(main, "AiohttpSession", FakeAiohttpSession)
    monkeypatch.setattr(main, "db", FakeDB())

    cfg = {
        "use_proxy": True,
        "proxy_host": "proxy.example",
        "proxy_port": "8080",
        "proxy_type": "",
        "mtproto_key": "",
        "proxy_user": "",
        "proxy_pass": "",
    }

    telethon_proxy, mtproto_kwargs, bot_session = main._setup_proxy(cfg)

    assert proxy_check.resolve_proxy_type(cfg) == "http"
    assert telethon_proxy is not None
    assert telethon_proxy["proxy_type"] == "http"
    assert mtproto_kwargs is None
    assert isinstance(bot_session, FakeAiohttpSession)
    assert bot_session.proxy == "http://proxy.example:8080"
