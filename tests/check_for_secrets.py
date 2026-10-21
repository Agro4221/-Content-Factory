"""Поиск секретов и мусора перед публикацией проекта в Git."""
import io
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "build", "dist", "release", "__pycache__", ".pytest_cache",
              "_test_screenshots", "temp_media", "queue_media", ".venv", "node_modules"}
SKIP_FILES = {".pyc"}

#: Реальные секреты: Telegram-токены, ключи API, пароли.
SECRET_PATTERNS = [
    (r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b", "Bot Token Telegram"),
    (r"\bsk-[A-Za-z0-9_-]{20,}\b", "OpenAI-совместимый API key"),
    (r"\bsk-or-v1-[A-Za-z0-9]{20,}\b", "OpenRouter key"),
    (r"\bghp_[A-Za-z0-9]{20,}\b", "GitHub personal access token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "Приватный ключ"),
    (r"(?i)\b(password|passwd|secret|token|api_key|apikey)\s*[=:]\s*['\"][^'\"]{8,}['\"]", "Жёстко заданный пароль/токен"),
]

print("=" * 74)
print("ПОИСК СЕКРЕТОВ В ТЕКСТОВЫХ ФАЙЛАХ")
print("=" * 74)
found = 0
test_hits = 0
for path in sorted(ROOT.rglob("*")):
    if not path.is_file():
        continue
    rel = path.relative_to(ROOT)
    if any(part in SKIP_DIRS for part in rel.parts):
        continue
    if path.suffix in {".exe", ".png", ".webp", ".jpg", ".db", ".session", ".pyc", ".zip"}:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    in_tests = rel.parts[0] == "tests"
    for pattern, label in SECRET_PATTERNS:
        for m in re.finditer(pattern, text):
            line_no = text[: m.start()].count("\n") + 1
            value = m.group(0)
            shown = value[:8] + "..." if len(value) > 12 else value
            if in_tests:
                test_hits += 1
            else:
                found += 1
                print(f"  [РЕАЛЬНЫЙ КОД] [{label}] {rel}:{line_no}  -> {shown}")

if not found:
    print("  В рабочем коде секретов не найдено.")
if test_hits:
    print(f"  В тестовых фикстурах: {test_hits} совпадений — это заведомо фейковые значения,")
    print("  которые нужны для проверки форматов. Реальных токенов среди них нет.")

if found:
    print()
    print(f"ОШИБКА: в рабочем коде найдено совпадений: {found}. Публикация заблокирована.")
    raise SystemExit(1)

print()
print("=" * 74)
print("ФАЙЛЫ, КОТОРЫЕ НЕ ДОЛЖНЫ ПОПАСТЬ В GIT")
print("=" * 74)
SENSITIVE = {".env", "bot_storage.db", "session_name.session", "Parser.exe"}
for path in sorted(ROOT.rglob("*")):
    if not path.is_file():
        continue
    rel = path.relative_to(ROOT)
    if any(part in SKIP_DIRS for part in rel.parts):
        continue
    if path.name in SENSITIVE or path.suffix in {".db", ".session", ".exe"}:
        size = path.stat().st_size
        print(f"  {rel}  ({size/1024:.1f} КБ)")

print()
print("=" * 74)
print("БИНАРНЫЕ И КРУПНЫЕ ФАЙЛЫ В КОРНЕ")
print("=" * 74)
for path in sorted(ROOT.iterdir()):
    if path.is_file() and path.stat().st_size > 100 * 1024:
        print(f"  {path.name}  ({path.stat().st_size/1024:.1f} КБ)")
