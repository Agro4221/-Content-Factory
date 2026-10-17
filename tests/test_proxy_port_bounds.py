"""Oversized persisted proxy ports must fail closed rather than crash."""
from __future__ import annotations

import asyncio

import pytest

import main
import proxy_check


def test_port_parsers_reject_oversized_decimal_strings() -> None:
    raw = "9" * 5000

    assert main._validate_port(raw) is None
    assert proxy_check._validate_port(raw) is None


def test_setup_proxy_rejects_oversized_persisted_port_as_configuration_error() -> None:
    with pytest.raises(main.ProxyConfigurationError, match="proxy_port"):
        main._setup_proxy(
            {
                "use_proxy": True,
                "proxy_type": "http",
                "proxy_host": "proxy.example",
                "proxy_port": "9" * 5000,
                "proxy_user": "",
                "proxy_pass": "",
                "mtproto_key": "",
            }
        )


def test_proxy_check_rejects_oversized_persisted_port_without_connecting(monkeypatch) -> None:
    async def should_not_connect(*_args, **_kwargs):
        raise AssertionError("invalid port must be rejected before connecting")

    monkeypatch.setattr(proxy_check, "_test_http", should_not_connect)

    result = asyncio.run(
        proxy_check.check_proxy(
            {
                "use_proxy": "1",
                "proxy_type": "http",
                "proxy_host": "proxy.example",
                "proxy_port": "9" * 5000,
            }
        )
    )

    assert result["ok"] is False
    assert result["reachable"] is False
