"""Regression tests for explicit failures from the posts statistics endpoint."""
from __future__ import annotations

import asyncio
import json

from fastapi.responses import JSONResponse

import Site


def test_stats_endpoint_returns_http_500_without_fake_zero_counters(
    monkeypatch,
) -> None:
    def fail_to_build_stats() -> dict:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(Site, "_build_posts_stats", fail_to_build_stats)

    response = asyncio.run(Site.api_stats_posts())

    assert isinstance(response, JSONResponse)
    assert response.status_code == 500
    # Внутренние подробности (имена таблиц, SQL, пути) в ответ не
    # попадают: о них пользователю знать не нужно, а машину он выдать может.
    payload = json.loads(response.body)
    assert payload["status"] == "error"
    assert "database unavailable" not in payload["msg"]
    assert "database unavailable" not in json.dumps(payload)


def test_stats_endpoint_preserves_success_payload(monkeypatch) -> None:
    expected = {"published_total": 12, "skipped_total": 3}
    monkeypatch.setattr(Site, "_build_posts_stats", lambda: expected)

    response = asyncio.run(Site.api_stats_posts())

    assert response == expected
