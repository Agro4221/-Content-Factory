"""Проверяет работу с разными форматами ответа ИИ и таймаут."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


def test_extracts_openai_style() -> None:
    data = {"choices": [{"message": {"content": "ТЕМА: общее\nТЕКСТ: привет"}}]}
    assert "привет" in main._extract_ai_content(data)


def test_extracts_content_parts_list() -> None:
    data = {"choices": [{"message": {"content": [{"type": "text", "text": "часть "},
                                                  {"type": "text", "text": "две"}]}}]}
    assert main._extract_ai_content(data) == "часть две"


def test_extracts_ollama_style() -> None:
    data = {"message": {"content": "ответ ollama"}}
    assert main._extract_ai_content(data) == "ответ ollama"


def test_extracts_anthropic_style() -> None:
    data = {"content": [{"type": "text", "text": "ответ anthropic"}]}
    assert main._extract_ai_content(data) == "ответ anthropic"


def test_extracts_output_text() -> None:
    assert main._extract_ai_content({"output_text": "ответ responses api"}) == "ответ responses api"


def test_extracts_choices_text_field() -> None:
    assert main._extract_ai_content({"choices": [{"text": "старый формат"}]}) == "старый формат"


def test_extract_returns_empty_for_unknown_shape() -> None:
    assert main._extract_ai_content({"unexpected": 1}) == ""
    assert main._extract_ai_content("строка") == ""


def test_strip_reasoning_blocks() -> None:
    raw = "<think>размышление модели</think>ТЕМА: общее\nТЕКСТ: текст"
    assert main._strip_reasoning_blocks(raw) == "ТЕМА: общее\nТЕКСТ: текст"


def test_strip_reasoning_blocks_keeps_plain_text() -> None:
    assert main._strip_reasoning_blocks("обычный текст") == "обычный текст"


def test_ai_timeout_uses_setting() -> None:
    assert main._ai_timeout_seconds("420") == 420


def test_ai_timeout_falls_back_on_invalid_value() -> None:
    assert main._ai_timeout_seconds("") == main.AI_REQUEST_TIMEOUT_S
    assert main._ai_timeout_seconds("не число") == main.AI_REQUEST_TIMEOUT_S
    assert main._ai_timeout_seconds("1") == main.AI_REQUEST_TIMEOUT_S


def test_ai_timeout_is_capped() -> None:
    assert main._ai_timeout_seconds("99999") == 3600
