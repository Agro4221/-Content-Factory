"""Пути запуска приложения и упакованных ресурсов в Windows."""
from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    """Определяет запуск из собранного исполняемого файла."""
    return bool(getattr(sys, "frozen", False))


def app_data_dir() -> str:
    """Возвращает каталог для базы, сессии Telegram, настроек и медиа."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir() -> str:
    """Возвращает каталог упакованных ресурсов PyInstaller."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", app_data_dir())
    return app_data_dir()


BASE_DIR: str = app_data_dir()
BUNDLE_DIR: str = bundle_dir()
