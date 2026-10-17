"""Runtime safeguards for enabled proxies: never silently fall back to direct networking."""
from __future__ import annotations

import pytest

import main


def _cfg(**overrides) -> dict:
    cfg = {
        "use_proxy": True,
        "proxy_type": "http",
        "proxy_host": "proxy.example",
        "proxy_port": "8080",
        "proxy_user": "",
        "proxy_pass": "",
        "mtproto_key": "",
    }
    cfg.update(overrides)
    return cfg


@pytest.mark.parametrize(
    "cfg",
    [
        _cfg(proxy_host=""),
        _cfg(proxy_port="not-a-port"),
        _cfg(proxy_port="0"),
        _cfg(proxy_type="mtproto", mtproto_key=""),
        _cfg(proxy_type="unknown"),
    ],
    ids=["missing-host", "non-numeric-port", "out-of-range-port", "missing-mtproto-secret", "unknown-type"],
)
def test_invalid_enabled_proxy_never_returns_direct_connection(monkeypatch, cfg: dict) -> None:
    with pytest.raises(main.ProxyConfigurationError):
        main._setup_proxy(cfg)


def test_socks_without_aiohttp_socks_does_not_fall_back_to_direct(monkeypatch) -> None:
    monkeypatch.setattr(main, "_HAS_SOCKS", False)

    with pytest.raises(main.ProxyConfigurationError, match="aiohttp-socks"):
        main._setup_proxy(_cfg(proxy_type="socks5", proxy_port="1080"))


def test_disabled_proxy_keeps_direct_connection_mode() -> None:
    assert main._setup_proxy(_cfg(use_proxy=False, proxy_host="", proxy_port="")) == (
        None,
        None,
        None,
    )
