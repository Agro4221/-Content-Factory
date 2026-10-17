"""Regression tests for active-source dashboard counts."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from database import DB
import Site


@pytest.fixture
def db_with_published_posts():
    db = object.__new__(DB)
    db._lock = threading.Lock()
    db.conn = sqlite3.connect(":memory:")
    db.conn.execute(
        "CREATE TABLE published_posts (source_name TEXT, created_at TEXT)"
    )
    try:
        yield db
    finally:
        db.conn.close()


def test_active_source_count_includes_sources_outside_top_five(
    db_with_published_posts: DB,
) -> None:
    db = db_with_published_posts
    since = "2026-10-16 00:00:00"
    recent = "2026-10-17 08:00:00"
    rows = [(f"@source{n}", recent) for n in range(1, 8)]
    rows.extend(
        [
            ("@source1", recent),
            ("@older-source", "2026-10-15 23:59:59"),
            (None, recent),
            ("", recent),
        ]
    )
    db.conn.executemany(
        "INSERT INTO published_posts (source_name, created_at) VALUES (?, ?)",
        rows,
    )

    assert len(db.top_published_sources(since, limit=5)) == 5
    assert db.count_active_published_sources_since(since) == 7


def test_dashboard_uses_full_active_source_count(monkeypatch: pytest.MonkeyPatch) -> None:
    log_type_queries: list[tuple[str, ...]] = []

    class FakeDB:
        def sqlite_datetime(self, modifier: str) -> str:
            return {
                "start of day": "2026-10-17 00:00:00",
                "-24 hours": "2026-10-16 08:00:00",
                "-1 hour": "2026-10-17 07:00:00",
            }[modifier]

        def count_total(self, _table: str) -> int:
            return 0

        def count_published_since(self, _since: str) -> int:
            return 0

        def count_skipped_since(self, _since: str) -> int:
            return 0

        def top_skipped_reasons(self, _since: str) -> dict[str, int]:
            return {}

        def count_logs_by_type(self, _types: tuple[str, ...], _since: str) -> int:
            log_type_queries.append(_types)
            return 4 if _types == ("OK-", "SUCCESS") else 0

        def last_published(self) -> tuple[None, None]:
            return None, None

        def last_log_by_prefix(self, _prefix: str):
            return None

        def top_published_sources(self, _since: str, limit: int = 5):
            return [(f"@source{n}", 1) for n in range(1, min(limit, 5) + 1)]

        def count_active_published_sources_since(self, _since: str) -> int:
            return 7

        def get_all_settings(self) -> dict[str, str]:
            return {
                "sources": "\n".join(f"@source{n}" for n in range(1, 8)),
                "topic_channels": "{}",
                "ai_mode": "LOCAL",
                "local_model": "test-model",
            }

    monkeypatch.setattr(Site, "db", FakeDB())

    stats = Site._build_posts_stats()

    assert len(stats["top_sources_24h"]) == 5
    assert stats["active_sources_24h"] == 7
    assert stats["successes_24h"] == 4
    assert ("OK-", "SUCCESS") in log_type_queries
    assert ("SYS-",) not in log_type_queries
