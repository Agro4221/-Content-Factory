"""Загрузка переменных окружения из .env рядом с приложением."""
from __future__ import annotations

import os
import shutil

from paths import BASE_DIR, BUNDLE_DIR

_ENV_LOADED = False


def load_environment() -> str:
    """
    Создаёт .env из .env.example при первом запуске и подгружает dotenv.
    Возвращает путь к использованному .env (или пустую строку).
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return os.path.join(BASE_DIR, ".env")

    env_path = os.path.join(BASE_DIR, ".env")
    example_path = os.path.join(BASE_DIR, ".env.example")
    if not os.path.isfile(example_path):
        bundled = os.path.join(BUNDLE_DIR, ".env.example")
        if os.path.isfile(bundled):
            example_path = bundled

    if not os.path.isfile(env_path) and os.path.isfile(example_path):
        try:
            shutil.copyfile(example_path, env_path)
            print("--> [СИСТЕМА] Создан файл .env из .env.example")
            print("--> [СИСТЕМА] Заполните ключи в .env (лицензия, API) при необходимости.")
        except OSError as exc:
            print(f"!!! [СИСТЕМА] Не удалось создать .env: {exc}")

    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        # python-dotenv может ставиться deps.py после первого импорта config —
        # не помечаем загрузку завершённой, чтобы повторить на следующем вызове.
        return env_path if os.path.isfile(env_path) else ""

    _ENV_LOADED = True
    return env_path if os.path.isfile(env_path) else ""
