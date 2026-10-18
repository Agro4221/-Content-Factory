"""Проверяет CSS/JavaScript админ-панели, загружаемые как отдельные ресурсы."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "admin.html"
CSS_PATH = ROOT / "admin.css"
JS_PATH = ROOT / "admin.js"


def check_admin_css(css: str) -> list[str]:
    """Catch structural CSS regressions that can hide routes or break layouts."""
    errors: list[str] = []
    critical_rules = (
        (r"^\s*\.split-menu-status\s*\{", ".split-menu-status"),
        (r"^\s*\.split-menu-status\.ready\s*\{", ".split-menu-status.ready"),
        (r"^\s*\.route\s*\{", ".route"),
        (r"^\s*\.route\.active\s*\{", ".route.active"),
        (r"^\s*@media\s*\(max-width:\s*1100px\)\s*\{", "@media max-width 1100px"),
        (r"^\s*@media\s*\(max-width:\s*860px\)\s*\{", "@media max-width 860px"),
        (r"^\s*@media\s*\(max-width:\s*540px\)\s*\{", "@media max-width 540px"),
    )

    def depth_before(css_text: str, limit: int) -> int:
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
                    index += 2
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

    for pattern, label in critical_rules:
        match = re.search(pattern, css, re.MULTILINE)
        if match is None:
            errors.append(f"Не найден CSS-блок {label}.")
            continue
        opening_brace = css.find("{", match.start(), match.end())
        nesting = depth_before(css, opening_brace)
        if nesting != 0:
            errors.append(f"CSS-блок {label} неожиданно вложен на уровне {nesting}.")

    responsive_block = re.search(
        r"@media\s*\(max-width:\s*1100px\)\s*\{\s*"
        r"\.type-grid\s*\{[^{}]*\}\s*"
        r"\.content-inner\s*\{[^{}]*\}\s*\}",
        css,
        re.DOTALL,
    )
    if responsive_block is None:
        errors.append("Блок адаптивной вёрстки для ширины до 1100px повреждён.")

    defined_vars = set(re.findall(r"(--[\w-]+)\s*:", css))
    used_vars = set(re.findall(r"var\(\s*(--[\w-]+)", css))
    missing_vars = sorted(used_vars - defined_vars)
    if missing_vars:
        errors.append("Не определены CSS-переменные: " + ", ".join(missing_vars) + ".")

    final_depth = depth_before(css, len(css))
    if final_depth != 0:
        errors.append(f"CSS-блоки не сбалансированы (итоговая глубина {final_depth}).")
    return errors


def main() -> int:
    if shutil.which("node") is None:
        print("Node.js не найден. Установите Node.js 20+ для проверки admin.js.", file=sys.stderr)
        return 2

    if not HTML_PATH.is_file() or not CSS_PATH.is_file() or not JS_PATH.is_file():
        missing = [path.name for path in (HTML_PATH, CSS_PATH, JS_PATH) if not path.is_file()]
        print("Не найдены ресурсы админки: " + ", ".join(missing), file=sys.stderr)
        return 1

    html = HTML_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    javascript = JS_PATH.read_text(encoding="utf-8")

    if 'href="/admin.css"' not in html:
        print("admin.html должен подключать /admin.css.", file=sys.stderr)
        return 1
    if 'src="/admin.js"' not in html:
        print("admin.html должен подключать /admin.js.", file=sys.stderr)
        return 1
    if re.search(r"<style\b", html, re.IGNORECASE):
        print("CSS должен храниться в admin.css, а не во встроенном style-блоке.", file=sys.stderr)
        return 1

    css_errors = check_admin_css(css)
    if css_errors:
        print("Ошибки структуры CSS админ-панели:", file=sys.stderr)
        for error in css_errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    stale_calls = (
        "refreshBehaviorStatus()",
        "refreshReviewsStatus()",
        "syncBehaviorFormFromSettings(",
        "syncReviewsFormFromSettings(",
    )
    found_stale_calls = [call for call in stale_calls if call in javascript]
    if found_stale_calls:
        print("Найдены вызовы удалённых модулей: " + ", ".join(found_stale_calls), file=sys.stderr)
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
        print("admin.js не прошёл синтаксическую проверку:", file=sys.stderr)
        print(result.stderr or result.stdout, file=sys.stderr)
        return result.returncode

    print("OK: admin.css валиден по структурным проверкам, admin.js разбирается без ошибок.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
