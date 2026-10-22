"""Аудит интерактивных элементов админки: у каждого должен быть обработчик.

Ищет кнопки, поля ввода, переключатели и ссылки, сопоставляет их с
обработчиками (inline-атрибутами, addEventListener или функциями раздела)
и сообщает о неподключённых элементах.
"""
import re
from pathlib import Path

PATH = str(Path(__file__).resolve().parents[1] / "admin.html")
html = open(PATH, encoding="utf-8").read()
# В документе несколько блоков <script>: основной логики — самый крупный.
script_blocks = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
script = max(script_blocks, key=len) if script_blocks else ""
style_blocks = re.findall(r"<style>(.*?)</style>", html, re.DOTALL)
style = max(style_blocks, key=len) if style_blocks else ""

# Функции, объявленные в скрипте.
functions = set(re.findall(r"function ([A-Za-z_$][\w$]*)\s*\(", script))

print("=" * 70)
print("ИНТЕРАКТИВНЫЕ ЭЛЕМЕНТЫ")
print("=" * 70)

problems = []
rows = []

# Элемент считается подключённым, если у него есть inline-обработчик
# или если его подключение выводится из разметки по data-атрибуту.
DATA_WIRING = {
    "nav-tab": "initTabs",
    "secret-btn": "setupSecretControls",
    "route-remove": "route-remove",
}
def wired_from_script(ident: str, cls: str) -> bool:
    for marker, function_name in DATA_WIRING.items():
        if marker in cls and function_name in script:
            return True
    if not ident or ident == "(без id)":
        return False
    return bool(re.search(r"['\"]" + re.escape(ident) + r"['\"]", script))

# Кнопки и ссылки с inline-обработчиком или без него.
for m in re.finditer(r"<(button|a)\b([^>]*)>", html):
    tag, attrs = m.group(1), m.group(2)
    ident = re.search(r'id="([^"]+)"', attrs)
    ident = ident.group(1) if ident else "(без id)"
    inline = re.findall(r'on(?:click|change)="([A-Za-z_$][\w$]*)\(', attrs)
    cls = re.search(r'class="([^"]*)"', attrs)
    cls = cls.group(1) if cls else ""
    if not inline and not wired_from_script(ident, cls):
        problems.append(f"{tag} {ident} ({cls}) — обработчик не найден")
    for fn in inline:
        if fn not in functions:
            problems.append(f"{tag} {ident} — вызов несуществующей функции {fn}()")
    rows.append((tag, ident, cls, inline, "inline" if inline else ("из скрипта" if wired_from_script(ident, cls) else "НЕТ")))

# Поля ввода.
print()
for m in re.finditer(r"<(input|select|textarea)\b([^>]*)>", html):
    tag, attrs = m.group(1), m.group(2)
    ident = re.search(r'id="([^"]+)"', attrs)
    ident = ident.group(1) if ident else "(без id)"
    itype = re.search(r'type="([^"]+)"', attrs)
    itype = itype.group(1) if itype else tag
    inline = re.findall(r'on(?:click|change|input)="([A-Za-z_$][\w$]*)\(', attrs)
    for fn in inline:
        if fn not in functions:
            problems.append(f"{tag}#{ident} — вызов несуществующей функции {fn}()")
    rows.append((tag + "[" + itype + "]", ident, "", inline, "inline" if inline else "—"))

print(f"{'тип':<18}{'id':<26}{'обработчики':<44}источник")
print("-" * 100)
for tag, ident, cls, inline, src in rows:
    handlers = ", ".join(inline) if inline else ""
    if src == "из скрипта":
        handlers = "добавляется из скрипта"
    print(f"{tag:<18}{ident:<26}{handlers:<44}{src}")

print()
print("=" * 70)
print("ПРОБЛЕМЫ")
print("=" * 70)
if problems:
    for p in problems:
        print("  [!]", p)
else:
    print("  Неподключённых интерактивных элементов не найдено.")

# Проверка: каждый id из скрипта существует в разметке.
print()
print("=" * 70)
print("СООТВЕТСТВИЕ ID")
print("=" * 70)
referenced = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", script))
referenced |= set(re.findall(r"\$\w*\(['\"]([^'\"]+)['\"]\)", script))
present = set(re.findall(r'id="([^"]+)"', html))
missing = sorted(referenced - present)
print("Ссылок из скрипта:", len(referenced))
print("Отсутствуют в разметке:", missing or "нет")

# Проверка: нет ли остаточных emoji в интерфейсе.
print()
print("=" * 70)
print("EMOJI В ИНТЕРФЕЙСЕ")
print("=" * 70)
body = re.sub(r"<script>.*?</script>", "", html, flags=re.DOTALL)
body = re.sub(r"<style>.*?</style>", "", body, flags=re.DOTALL)
emoji = re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\ufe0f]", body)
print("Найдено:", len(emoji), "->", sorted(set(emoji)) if emoji else "нет")
