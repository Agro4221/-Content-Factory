"""Regression tests for Telegram text truncation."""
from __future__ import annotations

import main


MARKER = "\n\n[…обрезано…]"


def test_long_text_without_spaces_is_preserved() -> None:
    raw = "X" * 5000

    result = main._truncate_telegram_text(raw, 4096)

    assert len(result) <= 4096
    assert result.startswith("X" * 100)
    assert result.removesuffix(MARKER)
    assert result != MARKER


def test_long_text_with_spaces_truncates_at_word_boundary() -> None:
    raw = "новость " * 1500

    result = main._truncate_telegram_text(raw, 4096)

    content = result.removesuffix(MARKER)
    assert len(result) <= 4096
    assert result.endswith(MARKER)
    assert content.endswith("новость")
    assert not content.endswith("новос")


def test_text_within_limit_is_unchanged() -> None:
    raw = "Обычный короткий текст"

    assert main._truncate_telegram_text(raw, 4096) == raw
