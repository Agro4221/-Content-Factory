"""Подготовка консоли к выводу кириллицы.

Зачем это нужно
---------------
В Windows кодировка консоли по умолчанию зависит от языка системы:
русская Windows использует cp1251, английская — cp1252. Если приложение
печатает кириллицу в консоль с cp1252, `print` падает с
`UnicodeEncodeError` — процесс падает уже на сообщении об ошибке, и
пользователь не видит причину.

Модуль переводит `stdout` и `stderr` в UTF-8 с заменой непечатных
символов. Для консоли Windows это корректно: слой `_io._WindowsConsoleIO`
сам декодирует переданные байты как UTF-8 и выводит их через
`WriteConsoleW`. При перенаправлении вывода в файл или в конвейер
получается обычный UTF-8 без BOM.

Модуль ничего не печатает сам и не зависит от сторонних пакетов.
"""
from __future__ import annotations

import contextlib
import sys


def setup_console() -> None:
    """Переводит стандартные потоки вывода в UTF-8.

    Ошибки не выбрасываются: если поток уже закрыт или недоступен,
    приложение продолжает работать как раньше.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # Поток может быть закрыт, перенаправлен или не поддерживать
        # перенастройку — в этом случае продолжаем как есть.
        with contextlib.suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace")


setup_console()
