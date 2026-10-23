"""Настройка стандартного вывода приложения.

В Windows кодировка консоли зависит от системных параметров. Если кодировка
потока не поддерживает выводимые символы, возникает ``UnicodeEncodeError`` и
исходное сообщение об ошибке может потеряться.

Модуль настраивает ``stdout`` и ``stderr`` на UTF-8 с заменой символов,
которые не удаётся представить. Он не выводит сообщения и не требует
сторонних зависимостей.
"""
from __future__ import annotations

import contextlib
import sys


def setup_console() -> None:
    """Настраивает стандартные потоки вывода на UTF-8.

    Если поток недоступен или не поддерживает перенастройку, запуск
    приложения продолжается без изменения этого потока.
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
