"""Regression tests for bounded pagination on the local admin logs endpoint."""
from __future__ import annotations

import asyncio

import pytest

import Site


@pytest.mark.parametrize(
    ("requested_limit", "expected_limit"),
    [
        (-10, 1),
        (-1, 1),
        (0, 1),
        (1, 1),
        (200, 200),
        (201, 200),
        (1000, 200),
    ],
)
def test_logs_endpoint_clamps_limit(
    monkeypatch: pytest.MonkeyPatch,
    requested_limit: int,
    expected_limit: int,
) -> None:
    captured: dict[str, int] = {}

    def fake_get_logs(*, limit: int, offset: int) -> list[tuple[str, str, str]]:
        captured["limit"] = limit
        captured["offset"] = offset
        return []

    monkeypatch.setattr(Site.db, "get_logs", fake_get_logs)

    result = asyncio.run(
        Site.api_get_logs(limit=requested_limit, page=1, module=None)
    )

    assert result == []
    assert captured == {"limit": expected_limit, "offset": 0}
