"""Пути приложения: обычный запуск и сборка (Windows .exe, macOS .app).

Данные приложения (база, сессия Telegram, медиа, .env) должны лежать там,
куда пользователь может писать, и не должны пропадать при обновлении.

На macOS собранное приложение лежит как ``ContentFactory.app`` — часто в
``/Applications``, куда обычному пользователю писать нельзя. Поэтому на
macOS данные хранятся в ``~/Library/Application Support/ContentFactory``,
как это принято для приложений macOS. Ресурсы (панель, картинки) при этом
читаются изнутри бандла — это нормально, их только читают.
"""
from __future__ import annotations

import os
import sys

#: Имя папки с данными приложения в Library.
MACOS_APP_SUPPORT_NAME = "ContentFactory"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _macos_data_dir() -> str:
    """Каталог данных приложения на macOS."""
    base = os.path.join(
        os.path.expanduser("~"), "Library", "Application Support", MACOS_APP_SUPPORT_NAME
    )
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        # Домашний каталог недоступен — тогда пишем рядом с исходниками,
        # чтобы приложение всё же запустилось.
        return os.path.dirname(os.path.abspath(__file__))
    return base


def app_data_dir() -> str:
    """Каталог данных (БД, .env, сессия, медиа)."""
    if sys.platform == "darwin":
        return _macos_data_dir()
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir() -> str:
    """Каталог упакованных ресурсов (HTML, статика) внутри _MEIPASS."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", app_data_dir())
    return app_data_dir()


BASE_DIR: str = app_data_dir()
BUNDLE_DIR: str = bundle_dir()
