"""Regression tests for independent legacy post-table migrations."""
from __future__ import annotations

import sqlite3

from database import DB


def database_for_connection(conn: sqlite3.Connection) -> DB:
    db = object.__new__(DB)
    db.conn = conn
    return db


def test_legacy_skipped_posts_are_migrated_when_published_posts_are_current() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE published_posts (post_key TEXT PRIMARY KEY, post_id TEXT, source_name TEXT)"
        )
        conn.execute(
            "INSERT INTO published_posts (post_key, post_id, source_name) VALUES (?, ?, ?)",
            ("@published:1", "1", "@published"),
        )
        conn.execute(
            "CREATE TABLE skipped_posts (post_id INTEGER PRIMARY KEY, source_name TEXT, reason TEXT)"
        )
        conn.execute(
            "INSERT INTO skipped_posts (post_id, source_name, reason) VALUES (?, ?, ?)",
            (22, "@legacy", "ads_word"),
        )

        database_for_connection(conn)._migrate_legacy_post_keys()

        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(skipped_posts)").fetchall()
        }
        assert "post_key" in columns
        row = conn.execute(
            "SELECT post_key, post_id, source_name, reason FROM skipped_posts"
        ).fetchone()
        assert row == ("@legacy:22", "22", "@legacy", "ads_word")
        # The already-current table must remain intact.
        assert conn.execute("SELECT post_key FROM published_posts").fetchall() == [
            ("@published:1",)
        ]
    finally:
        conn.close()


def test_both_legacy_tables_migrate_and_repeat_without_data_loss() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE published_posts (post_id INTEGER PRIMARY KEY, source_name TEXT)"
        )
        conn.execute(
            "INSERT INTO published_posts (post_id, source_name) VALUES (?, ?)",
            (11, "@published"),
        )
        conn.execute(
            "CREATE TABLE skipped_posts (post_id INTEGER PRIMARY KEY, source_name TEXT, reason TEXT)"
        )
        conn.execute(
            "INSERT INTO skipped_posts (post_id, source_name, reason) VALUES (?, ?, ?)",
            (22, "@skipped", "empty"),
        )

        db = database_for_connection(conn)
        db._migrate_legacy_post_keys()
        db._migrate_legacy_post_keys()

        assert conn.execute(
            "SELECT post_key, post_id, source_name FROM published_posts"
        ).fetchall() == [("@published:11", "11", "@published")]
        assert conn.execute(
            "SELECT post_key, post_id, source_name, reason FROM skipped_posts"
        ).fetchall() == [("@skipped:22", "22", "@skipped", "empty")]
    finally:
        conn.close()

def test_failed_legacy_migration_aborts_instead_of_leaving_broken_schema() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        # Corrupt/partial legacy schema: migration cannot preserve rows because
        # the historical table lacks its required source_name column.
        conn.execute("CREATE TABLE published_posts (post_id INTEGER PRIMARY KEY)")
        db = database_for_connection(conn)

        import pytest

        with pytest.raises(RuntimeError, match="published_posts migration failed"):
            db._migrate_legacy_post_keys()

        # The transaction must roll back the rename/create attempt rather than
        # leave a new but unusable post_key table behind.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(published_posts)")}
        assert columns == {"post_id"}
        assert conn.execute("SELECT COUNT(*) FROM published_posts").fetchone()[0] == 0
    finally:
        conn.close()
