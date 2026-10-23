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

    setup = main._setup_proxy(cfg)

    assert proxy_check.resolve_proxy_type(cfg) == "http"
    assert setup.telethon_proxy is not None
    assert setup.telethon_proxy["proxy_type"] == "http"
    assert setup.mtproto_kwargs is None
    assert isinstance(setup.bot_session, FakeAiohttpSession)
    assert setup.bot_session.proxy == "http://proxy.example:8080"

def test_explicit_http_type_overrides_stale_mtproto_key(monkeypatch) -> None:
    monkeypatch.setattr(main, "AiohttpSession", FakeAiohttpSession)
    monkeypatch.setattr(main, "db", FakeDB())

    def mtproto_must_not_be_built(*_args, **_kwargs):
        raise AssertionError("stale MTProto key must not override explicit HTTP type")

    monkeypatch.setattr(main, "build_mtproto_client_kwargs", mtproto_must_not_be_built)
    cfg = {
        "use_proxy": True,
        "proxy_host": "proxy.example",
        "proxy_port": "8080",
        "proxy_type": "HTTP",
        "mtproto_key": "stale-old-secret",
        "proxy_user": "",
        "proxy_pass": "",
    }

    setup = main._setup_proxy(cfg)

    assert proxy_check.resolve_proxy_type(cfg) == "http"
    assert setup.telethon_proxy is not None
    assert setup.telethon_proxy["proxy_type"] == "http"
    assert setup.mtproto_kwargs is None
    assert setup.bot_session.proxy == "http://proxy.example:8080"


def test_legacy_empty_proxy_type_can_still_infer_mtproto_from_key() -> None:
    cfg = {
        "proxy_type": "",
        "mtproto_key": "legacy-key",
    }

    assert proxy_check.resolve_proxy_type(cfg) == "mtproto"

