"""Первый запуск приложения: каталоги, подстановка настроек из .env."""
from __future__ import annotations

import os

from config import (
    APP_VERSION,
    ENV_DEFAULT_SETTINGS,
    QUEUE_MEDIA_DIR,
    TEMP_DIR,
    runtime_label,
)
from database import get_db
from env_loader import load_environment
from single_instance import enforce_single_instance


def prepare_runtime_directories() -> None:
    """Создаёт рабочие каталоги приложения."""
    load_environment()
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(QUEUE_MEDIA_DIR, exist_ok=True)


def seed_settings_from_env() -> int:
    """Подставляет в БД пустые поля из .env (не перезаписывает существующие)."""
    if not ENV_DEFAULT_SETTINGS:
        return 0
    db = get_db()
    written = 0
    for key, value in ENV_DEFAULT_SETTINGS.items():
        current = (db.get_setting(key, "") or "").strip()
        if not current and value:
            db.set_setting(key, value)
            written += 1
    if written:
        db.add_log("SYS-000", f"Из .env подставлено настроек: {written}")
    return written


def run_startup_bootstrap() -> None:
    """Вызывается из main.py до запуска API и бота."""
    prepare_runtime_directories()
    seed_settings_from_env()
    print(f"--> [СИСТЕМА] Content Factory v{APP_VERSION} ({runtime_label()})")


def ensure_single_instance() -> bool:
    """Проверяет, что приложение не запущено второй раз.

    Вторая копия закрывается: иначе две копии поделят файл сессии
    Telegram, и Telegram признает ключ использованным с двух адресов.
    """
    return enforce_single_instance()
