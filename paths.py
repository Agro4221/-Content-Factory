"""Пути приложения: обычный запуск и сборка PyInstaller (.exe)."""
from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_data_dir() -> str:
    """Каталог данных рядом с .exe или со скриптами (БД, .env, сессия)."""
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
