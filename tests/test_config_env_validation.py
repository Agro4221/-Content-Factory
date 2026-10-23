"""Guard import-time environment settings against malformed .env values."""
from __future__ import annotations

import pytest

import config


@pytest.mark.parametrize("raw", ["", "nope", "１２３", "-1", "0", "65536"])
def test_bounded_integer_environment_value_falls_back_safely(monkeypatch, raw: str) -> None:
    monkeypatch.setenv("TEST_PORT", raw)
    assert config._env_int("TEST_PORT", 8000, minimum=1, maximum=65535) == 8000


@pytest.mark.parametrize(("raw", "expected"), [("1", 1), ("8000", 8000), ("65535", 65535)])
def test_bounded_integer_environment_accepts_range(monkeypatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("TEST_PORT", raw)
    assert config._env_int("TEST_PORT", 8000, minimum=1, maximum=65535) == expected


@pytest.mark.parametrize("raw", ["nope", "nan", "inf", "-inf", "-1", "60.1"])
def test_bounded_float_environment_value_falls_back_safely(monkeypatch, raw: str) -> None:
    monkeypatch.setenv("TEST_DELAY", raw)
    assert config._env_float("TEST_DELAY", 2.5, minimum=0.0, maximum=60.0) == 2.5


@pytest.mark.parametrize(("raw", "expected"), [("0", 0.0), ("2.5", 2.5), ("60", 60.0)])
def test_bounded_float_environment_accepts_finite_range(monkeypatch, raw: str, expected: float) -> None:
    monkeypatch.setenv("TEST_DELAY", raw)
    assert config._env_float("TEST_DELAY", 2.5, minimum=0.0, maximum=60.0) == expected
