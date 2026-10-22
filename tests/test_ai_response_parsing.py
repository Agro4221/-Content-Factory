"""Разбор ответа модели: метки в любом оформлении, хештеги, размышления.

Локальные модели часто возвращают метки разделов в Markdown-выделении
(«**ТЕМА:** политика») и пишут хештеги в несколько строк. Если такой ответ
не распознаётся, тема не находится — пост уходит в «общее» или не
публикуется вовсе.
"""
from __future__ import annotations

import pytest

import main


@pytest.mark.parametrize(
    ("label", "response", "expected_topic"),
    [
        ("обычный формат", "ТЕМА: политика\nТЕКСТ: Текст.", "политика"),
        ("метки в bold", "**ТЕМА:** политика\n**ТЕКСТ:** Текст.", "политика"),
        ("метки в звёздочках", "*ТЕМА:* политика\n*ТЕКСТ:* Текст.", "политика"),
        ("метки в подчёркивании", "__ТЕМА__: политика\n__ТЕКСТ__: Текст.", "политика"),
        ("двоеточие вне выделения", "**ТЕМА**:\n**ТЕКСТ**:\nТекст.", "общее"),
        ("тема на следующей строке", "ТЕМА:\nполитика\nТЕКСТ: Текст.", "политика"),
        ("тема в bold", "**ТЕМА:** **политика**\nТЕКСТ: Текст.", "политика"),
        ("без темы", "ТЕКСТ: Текст.", "общее"),
    ],
)
def test_topic_is_recognised(label: str, response: str, expected_topic: str) -> None:
    topic, _ = main._parse_ai_response(response, "исходник")
    assert topic == expected_topic, label


def test_markdown_of_plain_text_is_not_treated_as_a_label() -> None:
    """Выделение в самом тексте не должно превращаться в метку."""
    response = "ТЕМА: политика\nТЕКСТ: Это **важная** новость.\nХЭШТЕГИ: #a"
    topic, text = main._parse_ai_response(response, "исходник")
    assert topic == "политика"
    assert "**важная**" in text


def test_hashtags_on_several_lines_are_collected() -> None:
    response = "ТЕМА: политика\nТЕКСТ: Текст.\nХЭШТЕГИ:\n#а\n#б\n#в"
    _, text = main._parse_ai_response(response, "исходник")
    assert "#а" in text
    assert "#б" in text
    assert "#в" in text


def test_hashtags_do_not_stay_inside_the_text() -> None:
    response = "ТЕМА: политика\nТЕКСТ: Текст.\nХЭШТЕГИ: #а"
    _, text = main._parse_ai_response(response, "исходник")
    body = text.split("\n\n")[0]
    assert "#а" not in body


def test_missing_text_section_is_reported() -> None:
    with pytest.raises(main.AIResponseFormatError):
        main._parse_ai_response("ТЕМА: политика\nХЭШТЕГИ: #а", "исходник")


@pytest.mark.parametrize(
    ("label", "response", "expected"),
    [
        ("закрытый блок", "<think>размышление</think>Ответ", "Ответ"),
        ("незакрытый блок", "<think>размышление без конца", ""),
        ("служебные маркеры", "<|thinking|>мысли<|end|>Ответ", ""),
        ("обычный текст", "Просто ответ", "Просто ответ"),
        ("пусто", "", ""),
    ],
)
def test_reasoning_blocks_are_removed(label: str, response: str, expected: str) -> None:
    assert main._strip_reasoning_blocks(response) == expected, label
