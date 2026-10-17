"""Regression tests for malformed Telegram bot tokens at startup."""
from __future__ import annotations

import pytest

import main


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-token",
        "12345",
        "12345:short",
        "user:some-password",
    ],
)
def test_invalid_bot_token_formats_are_rejected(token: str) -> None:
    assert main._is_valid_bot_token_format(token) is False


def test_well_formed_bot_token_format_is_accepted() -> None:
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi123456"

    assert main._is_valid_bot_token_format(token) is True
