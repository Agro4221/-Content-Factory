"""Разбор ответа модели: теги для маршрутизации и текст без хештегов.

Локальные модели часто возвращают метки разделов в Markdown-выделении
(«**ТЕКСТ:** …») и пишут хештеги в несколько строк. Теги нужны только
для выбора канала — в самом посте их быть не должно.
"""
from __future__ import annotations

import pytest

import main


@pytest.mark.parametrize(
    ("label", "response", "expected_tags"),
    [
        ("новый формат", "ТЕКСТ: Текст.\nТЕГИ: политика", ["политика"]),
        ("старый формат", "ТЕМА: политика\nТЕКСТ: Текст.", ["политика"]),
        ("хештеги", "ТЕКСТ: Текст.\nХЭШТЕГИ: #политика", ["политика"]),
        ("несколько тегов", "ТЕКСТ: Текст.\nТЕГИ: политика, спорт", ["политика", "спорт"]),
        ("метки в bold", "**ТЕКСТ:** Текст.\n**ТЕГИ:** политика", ["политика"]),
        ("без тегов", "ТЕКСТ: Текст.", []),
    ],
)
def test_tags_are_recognised(label: str, response: str, expected_tags: list[str]) -> None:
    tags, _ = main._parse_ai_response(response, "исходник")
    assert tags == expected_tags, label


def test_markdown_of_plain_text_is_not_treated_as_a_label() -> None:
    """Выделение в самом тексте не должно превращаться в метку."""
    response = "ТЕКСТ: Это **важная** новость.\nТЕГИ: политика"
    tags, text = main._parse_ai_response(response, "исходник")
    assert tags == ["политика"]
    assert "**важная**" in text


def test_hashtags_on_several_lines_are_collected() -> None:
    response = "ТЕКСТ: Текст.\nТЕГИ:\n#а\n#б\n#в"
    tags, _ = main._parse_ai_response(response, "исходник")
    assert tags == ["а", "б", "в"]


def test_hashtags_do_not_stay_inside_the_text() -> None:
    """Теги — только для маршрутизации, в посте их быть не должно."""
    response = "ТЕКСТ: Текст новости.\nТЕГИ: политика"
    tags, text = main._parse_ai_response(response, "исходник")
    assert tags == ["политика"]
    assert "политика" not in text
    assert "#" not in text


def test_hashtags_inside_the_text_are_removed_and_used_for_routing() -> None:
    response = "ТЕКСТ: Новость про #политику и #срочное.\nТЕГИ: политика"
    tags, text = main._parse_ai_response(response, "исходник")
    assert "политика" in tags
    assert "срочное" in tags
    assert "#" not in text


def test_missing_text_section_is_reported() -> None:
    with pytest.raises(main.AIResponseFormatError):
        main._parse_ai_response("ТЕГИ: политика", "исходник")


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
