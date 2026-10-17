"""Regression tests for rejecting non-ASCII proxy port digits."""
from __future__ import annotations

import asyncio

import main
import proxy_check
import Site


def test_proxy_check_port_requires_ascii_digits() -> None:
    assert proxy_check._validate_port("1080") == 1080
    assert proxy_check._validate_port("²") is None
    assert proxy_check._validate_port("１２３") is None


def test_runtime_port_requires_ascii_digits() -> None:
    assert main._validate_port("1080") == 1080
    assert main._validate_port("²") is None
    assert main._validate_port("１２３") is None


class FakeRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def json(self) -> dict:
        return self.payload


def test_proxy_test_api_returns_port_error_instead_of_raising() -> None:
    result = asyncio.run(
        Site.api_proxy_test(
            FakeRequest({
                "use_proxy": "1",
                "proxy_type": "HTTP",
                "proxy_host": "proxy.example",
                "proxy_port": "²",
            })
        )
    )

    assert result["ok"] is False
    assert result["reachable"] is False
    assert result["state"] == "error"
    assert result["message"] == "Некорректный порт"
