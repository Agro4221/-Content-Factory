"""Regression tests for malformed topic_channels JSON shapes in dashboard stats."""
from __future__ import annotations

import Site


class FakeStatsDB:
    def __init__(self, topic_channels: str) -> None:
        self.settings = {
            "ai_mode": "LOCAL",
            "local_model": "test-model",
            "sources": "@source1\n@source2",
            "topic_channels": topic_channels,
        }

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
        return 0

    def last_published(self) -> tuple[None, None]:
        return None, None

    def last_log_by_prefix(self, _prefix: str):
        return None

    def top_published_sources(self, _since: str, limit: int = 5):
        return []

    def count_active_published_sources_since(self, _since: str) -> int:
        return 0

    def get_all_settings(self) -> dict[str, str]:
        return self.settings


def test_non_object_json_shape_does_not_break_stats(monkeypatch) -> None:
    monkeypatch.setattr(Site, "db", FakeStatsDB("[]"))

    stats = Site._build_posts_stats()

    assert stats["topics_count"] == 0
    assert stats["topics_with_target"] == 0
    assert stats["sources_count"] == 2


def test_json_null_string_and_number_are_treated_as_empty_topics(monkeypatch) -> None:
    for raw in ("null", '"text"', "42", "true"):
        monkeypatch.setattr(Site, "db", FakeStatsDB(raw))

        stats = Site._build_posts_stats()

        assert stats["topics_count"] == 0
        assert stats["topics_with_target"] == 0


def test_valid_topic_object_keeps_topic_counts(monkeypatch) -> None:
    monkeypatch.setattr(
        Site,
        "db",
        FakeStatsDB('{"news":"@target","empty":""}'),
    )

    stats = Site._build_posts_stats()

    assert stats["topics_count"] == 2
    assert stats["topics_with_target"] == 1
