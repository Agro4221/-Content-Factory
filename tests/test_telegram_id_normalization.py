"""Telegram channel identifiers must use the correct type for each API."""
from __future__ import annotations

import main


def test_numeric_source_ids_are_passed_to_telethon_as_integers() -> None:
    value = main._source_entity("-100123456789")

    assert value == -100123456789
    assert isinstance(value, int)


def test_public_source_names_and_links_remain_strings() -> None:
    assert main._source_entity("@news_channel") == "@news_channel"
    assert main._source_entity("https://t.me/news_channel") == "https://t.me/news_channel"


def test_numeric_target_chat_ids_are_passed_to_bot_api_as_integers() -> None:
    value = main._target_chat_id("-100123456789")

    assert value == -100123456789
    assert isinstance(value, int)


def test_public_target_usernames_remain_strings() -> None:
    assert main._target_chat_id("@news_channel") == "@news_channel"
