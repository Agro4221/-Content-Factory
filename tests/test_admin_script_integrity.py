"""Целостность скриптов панели: обработчики должны быть определены в <script>.

Панель — один файл admin.html. Если функцию, на которую ссылается inline-обработчик
(onclick="..."), объявить после закрывающего </html> или вне <script>, браузер
не выполнит её: такой код попадает в текст страницы, а не в скрипт. Кнопка
молча не работает, а в консоли появляется ReferenceError.

Тест закрывает весь класс таких ошибок, а не одну конкретную функцию.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import Site


@pytest.fixture
def client():
    with TestClient(
        Site.app,
        client=("127.0.0.1", 12345),
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


def _script_blocks(html: str) -> str:
    """Содержимое всех <script> блоков панели."""
    return "\n".join(re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE))


def _inline_handlers(html: str) -> set[str]:
    """Имена функций, вызываемых из inline-обработчиков (onclick и т.п.)."""
    handlers: set[str] = set()
    for attribute in re.findall(r"\son[a-z]+\s*=\s*\"([^\"]*)\"", html, re.IGNORECASE):
        for call in re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", attribute):
            # Встроенные вызовы браузера (alert, confirm, event) — не наши функции.
            if call in {"alert", "confirm", "prompt", "event", "return", "if", "for", "while"}:
                continue
            handlers.add(call)
    return handlers


def _defined_names(script: str) -> set[str]:
    """Имена, объявленные в скрипте: функции, const/let/var, классы."""
    names: set[str] = set()
    patterns = (
        r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(",
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=",
        r"\bclass\s+([A-Za-z_$][\w$]*)\b",
    )
    for pattern in patterns:
        names.update(re.findall(pattern, script))
    return names


def test_inline_handlers_are_defined_inside_script(client: TestClient) -> None:
    """Каждая функция из onclick должна быть объявлена внутри <script>."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    script = _script_blocks(html)
    defined = _defined_names(script)

    missing = sorted(name for name in _inline_handlers(html) if name not in defined)
    assert not missing, (
        "Функции из inline-обработчиков не объявлены внутри <script>: "
        + ", ".join(missing)
    )


def test_admin_html_has_no_javascript_after_closing_html_tag(client: TestClient) -> None:
    """После </html> не должно оставаться исполняемого кода."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    closing = html.lower().rfind("</html>")
    assert closing != -1, "В панели должен быть закрывающий тег </html>"
    tail = html[closing + len("</html>"):].strip()
    assert not tail, f"После </html> остался лишний код: {tail[:200]!r}"


def test_script_blocks_are_balanced(client: TestClient) -> None:
    """Открывающих и закрывающих <script> должно быть поровну."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    opens = len(re.findall(r"<script\b", html, re.IGNORECASE))
    closes = len(re.findall(r"</script>", html, re.IGNORECASE))
    assert opens == closes, f"Непарные теги <script>: открыто {opens}, закрыто {closes}"
    assert opens > 0, "В панели должен быть хотя бы один <script>"


def test_ai_timeout_control_is_wired(client: TestClient) -> None:
    """Поле таймаута ИИ есть в разметке и связано с сохранением настроек."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text

    assert 'id="f-ai_timeout"' in html
    # Значение уходит в /api/settings вместе с остальными полями.
    assert "ai_timeout" in html
