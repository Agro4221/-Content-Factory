"""Защита от второго запуска приложения.

Telegram привязывает сессию пользователя к адресу, с которого она
подключается. Если запустить две копии приложения, они делят один файл
сессии, и Telegram считает, что ключ используется с двух адресов сразу.
После этого сессию приходится пересоздавать вручную.

Поэтому вторая копия должна тихо завершиться, а не биться за сессию.
Блокировка ставится на сам файл: она снимается сама, когда процесс
завершается, — «мёртвый» файл блокировки не остаётся.
"""
from __future__ import annotations

import contextlib
import os
import sys

from paths import BASE_DIR

#: Файл блокировки рядом с базой и сессией.
LOCK_FILE_NAME = "app.lock"

#: Держим открытый дескриптор, пока работает приложение: пока он открыт,
#: блокировка держится.
_lock_handle = None


def _lock_path() -> str:
    return os.path.join(BASE_DIR, LOCK_FILE_NAME)


def acquire_single_instance() -> bool:
    """Захватить право единственного запуска.

    Возвращает True, если приложение может работать, и False, если
    копия уже запущена.
    """
    global _lock_handle
    if _lock_handle is not None:
        return True

    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        # Файл держим открытым весь сеанс: пока дескриптор открыт, держится
        # и блокировка. Контекстный менеджер здесь не подходит.
        handle = open(_lock_path(), "a+b")  # noqa: SIM115
    except OSError:
        # Не смогли создать файл блокировки — не блокируем запуск:
        # лучше рискнуть дублем, чем не дать приложению работать.
        return True

    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False

    _lock_handle = handle
    return True


def release_single_instance() -> None:
    """Отпустить блокировку (вызывается при завершении)."""
    global _lock_handle
    if _lock_handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            _lock_handle.close()
        _lock_handle = None


def already_running_message() -> str:
    """Сообщение для пользователя при попытке второго запуска."""
    return (
        "Content Factory уже запущен. Второй экземпляр закрыт, чтобы не "
        "испортить сессию Telegram: два процесса не должны использовать "
        "один файл сессии одновременно."
    )


def enforce_single_instance() -> bool:
    """Проверить и, если нужно, завершить процесс.

    Возвращает True, когда приложение может продолжать работу.
    """
    if acquire_single_instance():
        return True
    print("!!! [СИСТЕМА] " + already_running_message(), file=sys.stderr)
    return False
