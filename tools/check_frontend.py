"""Проверяет встроенные CSS/JavaScript админ-панели.

Панель самодостаточна: стили и скрипт лежат внутри admin.html, поэтому
проверка работает с ними как с единым файлом сборки.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import console_setup  # noqa: E402  (импорт сам настраивает вывод в UTF-8)

HTML_PATH = ROOT / "admin.html"

# Правила CSS, потеря которых может скрыть раздел или сломать раскладку.
CRITICAL_CSS_RULES = (
    (r"^\s*\.appbar\s*\{", ".appbar"),
    (r"^\s*\.tabs\s*\{", ".tabs"),
    (r"^\s*\.nav-tab\s*\{", ".nav-tab"),
    (r"^\s*\.nav-tab\.active\s*\{", ".nav-tab.active"),
    (r"^\s*\.panel\s*\{", ".panel"),
    (r"^\s*\.panel\.active\s*\{", ".panel.active"),
    (r"^\s*\.status-strip\s*\{", ".status-strip"),
    (r"^\s*\.card\s*\{", ".card"),
    (r"^\s*\.block\s*\{", ".block"),
    (r"^\s*\.block-head\s*\{", ".block-head"),
    (r"^\s*\.grid\s*\{", ".grid"),
    (r"^\s*\.field\s*\{", ".field"),
    (r"^\s*\.secret-row\s*\{", ".secret-row"),
    (r"^\s*\.confirm-box\s*\{", ".confirm-box"),
    (r"^\s*\.action-bar\s*\{", ".action-bar"),
    (r"^\s*@media\s*\(max-width:\s*620px\)\s*\{", "@media max-width 620px"),
)

# Block calls to unsupported interface handlers from entering the panel bundle.
UNSUPPORTED_CALLS = (
    "refreshBehaviorStatus()",
    "refreshReviewsStatus()",
    "syncBehaviorFormFromSettings(",
    "syncReviewsFormFromSettings(",
)


def extract_block(html: str, tag: str) -> str:
    """Достаёт содержимое встроенного <style> или <script> из HTML."""
    match = re.search(
        rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, re.IGNORECASE | re.DOTALL
    )
    return match.group(1) if match else ""


def depth_before(css_text: str, limit: int) -> int:
    """Глубина вложенности блоков перед указанной позицией."""
    depth = 0
    in_comment = False
    quote = ""
    index = 0
    while index < limit:
        char = css_text[index]
        next_char = css_text[index + 1] if index + 1 < limit else ""
        if in_comment:
            if char == "*" and next_char == "/":
                in_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if char == "\\":
                index += 1
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and next_char == "*":
            in_comment = True
            index += 2
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return depth


def check_admin_css(css: str) -> list[str]:
    """Ловит структурные регрессии CSS, скрывающие разделы или ломающие раскладку."""
    errors: list[str] = []

    for pattern, label in CRITICAL_CSS_RULES:
        match = re.search(pattern, css, re.MULTILINE)
        if match is None:
            errors.append(f"Не найден CSS-блок {label}.")
            continue
        opening_brace = css.find("{", match.start(), match.end())
        nesting = depth_before(css, opening_brace)
        if nesting != 0:
            errors.append(f"CSS-блок {label} неожиданно вложен на уровне {nesting}.")

    defined_vars = set(re.findall(r"(--[\w-]+)\s*:", css))
    used_vars = set(re.findall(r"var\(\s*(--[\w-]+)", css))
    missing_vars = sorted(used_vars - defined_vars)
    if missing_vars:
        errors.append("Не определены CSS-переменные: " + ", ".join(missing_vars) + ".")

    final_depth = depth_before(css, len(css))
    if final_depth != 0:
        errors.append(f"CSS-блоки не сбалансированы (итоговая глубина {final_depth}).")

    return errors


def check_admin_html(html: str) -> list[str]:
    """Проверяет разметку панели."""
    errors: list[str] = []

    if 'href="/admin.css"' in html or 'src="/admin.js"' in html:
        errors.append(
            "Панель самодостаточна: внешние /admin.css и /admin.js не подключаются."
        )

    # Каждый id, к которому обращается скрипт, должен существовать в разметке.
    script = extract_block(html, "script")
    referenced = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", script))
    referenced |= set(re.findall(r"\$\w*\(['\"]#?([A-Za-z_][\w-]*)['\"]\)", script))
    present = set(re.findall(r"id=\"([^\"]+)\"", html))
    missing = sorted(referenced - present)
    if missing:
        errors.append("Скрипт обращается к отсутствующим элементам: " + ", ".join(missing) + ".")

    # Секреты не должны попадать в разметку или в код панели.
    for pattern in (r"\bbot_token\s*=\s*['\"][^'\"]+['\"]",):
        if re.search(pattern, script):
            errors.append("В коде панели найдено значение секрета.")

    # Основные элементы управления должны присутствовать.
    for element_id in (
        "btn-bot-toggle",
        "btn-refresh",
        "btn-save",
        "runtime-state",
        "topics-list",
        "log-view",
        "toast-container",
        "f-bot_token",
        "f-remote_api_key",
        "f-proxy_pass",
        "f-mtproto_key",
    ):
        if element_id not in present:
            errors.append(f"В разметке нет элемента #{element_id}.")

    # Полоса статуса и панель действий заданы классами.
    for class_name in ("status-strip", "action-bar"):
        if not re.search(r'class="[^"]*\b' + class_name + r'\b[^"]*"', html):
            errors.append(f"В разметке нет блока .{class_name}.")

    # Каждая вкладка навигации должна вести в свою панель.
    tabs = re.findall(r'class="nav-tab"[^>]*data-panel="([^"]+)"', html)
    panels = re.findall(r'class="panel"[^>]*data-panel="([^"]+)"', html)
    if not tabs:
        errors.append("В разметке нет навигации по разделам.")
    for tab in tabs:
        if tab not in panels:
            errors.append(f"Вкладка «{tab}» не имеет соответствующей панели.")
    for panel in panels:
        if panel not in tabs:
            errors.append(f"Панель «{panel}» недоступна из навигации.")

    return errors


def main() -> int:
    if shutil.which("node") is None:
        print("Node.js не найден. Установите Node.js 20+ для проверки скрипта панели.", file=sys.stderr)
        return 2

    if not HTML_PATH.is_file():
        print("Не найден admin.html.", file=sys.stderr)
        return 1

    html = HTML_PATH.read_text(encoding="utf-8")
    css = extract_block(html, "style")
    javascript = extract_block(html, "script")

    if not css.strip():
        print("admin.html не содержит встроенного <style> с стилями панели.", file=sys.stderr)
        return 1
    if not javascript.strip():
        print("admin.html не содержит встроенного <script> с логикой панели.", file=sys.stderr)
        return 1

    html_errors = check_admin_html(html)
    if html_errors:
        print("Ошибки разметки админ-панели:", file=sys.stderr)
        for error in html_errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    css_errors = check_admin_css(css)
    if css_errors:
        print("Ошибки структуры CSS админ-панели:", file=sys.stderr)
        for error in css_errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    found_unsupported_calls = [call for call in UNSUPPORTED_CALLS if call in javascript]
    if found_unsupported_calls:
        print("Найдены неподдерживаемые вызовы в панели: " + ", ".join(found_unsupported_calls), file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(javascript)
        temp_path = Path(handle.name)
    try:
        result = subprocess.run(
            ["node", "--check", str(temp_path)],
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        temp_path.unlink(missing_ok=True)
    if result.returncode:
        print("Скрипт панели не прошёл синтаксическую проверку:", file=sys.stderr)
        print(result.stderr or result.stdout, file=sys.stderr)
        return result.returncode

    print("OK: встроенные CSS и JS админ-панели проходят структурные и синтаксические проверки.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
