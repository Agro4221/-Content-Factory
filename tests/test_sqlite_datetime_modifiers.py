"""Regression tests for SQLite anchor date modifiers used by dashboard stats."""
from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from database import DB


@pytest.fixture
def db_without_connection() -> DB:
    # sqlite_datetime only uses date helpers and does not access the connection.
    return object.__new__(DB)


def parse_result(db: DB, modifier: str) -> datetime:
    return datetime.strptime(db.sqlite_datetime(modifier), "%Y-%m-%d %H:%M:%S")


def test_start_of_day_returns_local_midnight(db_without_connection: DB) -> None:
    result = parse_result(db_without_connection, "start of day")

    assert result.time() == time.min
    assert result.date() == datetime.now().date()


def test_start_of_month_returns_first_day_at_midnight(db_without_connection: DB) -> None:
    result = parse_result(db_without_connection, "start of month")

    assert result.day == 1
    assert result.time() == time.min


def test_start_of_year_returns_january_first_at_midnight(db_without_connection: DB) -> None:
    result = parse_result(db_without_connection, "start of year")

    assert (result.month, result.day) == (1, 1)
    assert result.time() == time.min


def test_relative_time_modifier_keeps_existing_behavior(db_without_connection: DB) -> None:
    before = datetime.now()
    result = parse_result(db_without_connection, "-24 hours")
    after = datetime.now()

    assert before - timedelta(hours=24) <= result <= after - timedelta(hours=24)
