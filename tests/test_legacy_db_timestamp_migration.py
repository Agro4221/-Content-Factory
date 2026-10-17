"""Regression tests for timestamp columns on populated legacy SQLite schemas."""
from __future__ import annotations

import sqlite3

from database import CURRENT_SCHEMA_VERSION, DB


def _make_legacy_db(path, *, include_created_at: bool) -> None:
    conn = sqlite3.connect(path)
    timestamp_columns = ", created_at DATETIME" if include_created_at else ""
    try:
        conn.execute(
            "CREATE TABLE published_posts (post_id TEXT PRIMARY KEY, source_name TEXT"
            + timestamp_columns
            + ")"
        )
        conn.execute(
            "CREATE TABLE skipped_posts (post_id TEXT PRIMARY KEY, source_name TEXT, reason TEXT"
            + timestamp_columns
            + ")"
        )
        if include_created_at:
            conn.execute(
                "INSERT INTO published_posts (post_id, source_name, created_at) VALUES (?, ?, ?)",
                ("17", "@news", "2024-03-04 05:06:07"),
            )
            conn.execute(
                "INSERT INTO skipped_posts (post_id, source_name, reason, created_at) VALUES (?, ?, ?, ?)",
                ("18", "@news", "empty", "2024-03-05 06:07:08"),
            )
        else:
            conn.execute(
                "INSERT INTO published_posts (post_id, source_name) VALUES (?, ?)",
                ("17", "@news"),
            )
            conn.execute(
                "INSERT INTO skipped_posts (post_id, source_name, reason) VALUES (?, ?, ?)",
                ("18", "@news", "empty"),
            )
        conn.commit()
    finally:
        conn.close()


def _columns(db: DB, table: str) -> set[str]:
    return {row[1] for row in db.conn.execute(f"PRAGMA table_info({table})")}


def _indexes(db: DB, table: str) -> set[str]:
    return {row[1] for row in db.conn.execute(f"PRAGMA index_list({table})")}


def test_populated_legacy_tables_migrate_without_default_alter_failure(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    _make_legacy_db(path, include_created_at=False)

    db = DB(str(path))
    try:
        published = db.conn.execute(
            "SELECT post_key, post_id, source_name, created_at FROM published_posts"
        ).fetchone()
        skipped = db.conn.execute(
            "SELECT post_key, post_id, source_name, reason, created_at FROM skipped_posts"
        ).fetchone()

        assert _columns(db, "published_posts") >= {
            "post_key", "post_id", "source_name", "created_at"
        }
        assert _columns(db, "skipped_posts") >= {
            "post_key", "post_id", "source_name", "reason", "created_at"
        }
        assert published[:3] == ("@news:17", "17", "@news")
        assert skipped[:4] == ("@news:18", "18", "@news", "empty")
        assert published[3]
        assert skipped[4]
        assert "idx_published_created_at" in _indexes(db, "published_posts")
        assert "idx_published_source_created_at" in _indexes(db, "published_posts")
        assert "idx_skipped_created_at" in _indexes(db, "skipped_posts")
        assert "idx_skipped_reason" in _indexes(db, "skipped_posts")
        assert db.count_published_since("2000-01-01 00:00:00") == 1
    finally:
        db.close()


def test_legacy_migration_preserves_existing_timestamps_and_new_rows_get_timestamps(tmp_path) -> None:
    path = tmp_path / "legacy_with_timestamps.db"
    _make_legacy_db(path, include_created_at=True)

    db = DB(str(path))
    try:
        published = db.conn.execute(
            "SELECT post_key, created_at FROM published_posts WHERE post_id = '17'"
        ).fetchone()
        skipped = db.conn.execute(
            "SELECT post_key, created_at FROM skipped_posts WHERE post_id = '18'"
        ).fetchone()
        assert published == ("@news:17", "2024-03-04 05:06:07")
        assert skipped == ("@news:18", "2024-03-05 06:07:08")

        db.save_post("@news", 19)
        db.save_skipped_post("@news", 20, "empty")
        assert db.conn.execute(
            "SELECT created_at FROM published_posts WHERE post_id = '19'"
        ).fetchone()[0]
        assert db.conn.execute(
            "SELECT created_at FROM skipped_posts WHERE post_id = '20'"
        ).fetchone()[0]
        assert db.conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
    finally:
        db.close()
