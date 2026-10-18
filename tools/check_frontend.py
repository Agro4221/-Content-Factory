"""Проверяет синтаксис встроенных JavaScript-блоков в admin.html."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "admin.html"
SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
 
STYLE_TAG = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.IGNORECASE | re.DOTALL)


def check_admin_css(css: str) -> list[str]:
    """Catch CSS nesting regressions that break the visual layout and page switching."""
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
        depth = depth_before(css, opening_brace)
        if depth != 0:
            errors.append(f"CSS-блок {label} неожиданно вложен на уровне {depth}.")

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

    # A balanced brace count alone misses accidental nesting; ensure the file
    # also returns to the top level at the end, ignoring comments and strings.
    final_depth = depth_before(css, len(css))
    if final_depth != 0:
        errors.append(f"CSS-блоки не сбалансированы (итоговая глубина {final_depth}).")

    return errors


def main() -> int:
    if shutil.which("node") is None:
        print("Node.js не найден. Установите Node.js 20+ для проверки admin.html.", file=sys.stderr)
        return 2

    html = HTML_PATH.read_text(encoding="utf-8")
    style_blocks = STYLE_TAG.findall(html)
    if len(style_blocks) != 1:
        print(f"Ожидался один встроенный CSS-блок, найдено: {len(style_blocks)}.", file=sys.stderr)
        return 1
    css_errors = check_admin_css(style_blocks[0])
    if css_errors:
        print("Ошибки структуры CSS админ-панели:", file=sys.stderr)
        for error in css_errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    scripts = [
        body
        for attrs, body in SCRIPT_TAG.findall(html)
        if not re.search(r"\bsrc\s*=", attrs, re.IGNORECASE) and body.strip()
    ]
    if not scripts:
        print(f"В {HTML_PATH.name} не найден встроенный JavaScript.", file=sys.stderr)
        return 1

    combined = "\\n".join(scripts)
    stale_calls = (
        "refreshBehaviorStatus()",
        "refreshReviewsStatus()",
        "syncBehaviorFormFromSettings(",
        "syncReviewsFormFromSettings(",
    )
    found_stale_calls = [call for call in stale_calls if call in combined]
    if found_stale_calls:
        print("Найдены вызовы удалённых модулей: " + ", ".join(found_stale_calls), file=sys.stderr)
        return 1

    for index, body in enumerate(scripts, start=1):
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(body)
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
            print(f"JavaScript-блок #{index} не прошёл синтаксическую проверку:", file=sys.stderr)
            print(result.stderr or result.stdout, file=sys.stderr)
            return result.returncode
    print(f"OK: синтаксис {len(scripts)} встроенного JavaScript-блока(ов) корректен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
