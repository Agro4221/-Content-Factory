"""Первый запуск, каталоги, проверка лицензии при старте приложения."""
from __future__ import annotations

import asyncio
import os

from Auth import get_hwid
from config import (
    APP_VERSION,
    ENV_DEFAULT_SETTINGS,
    LICENSE_SERVER_URL,
    LICENSE_VERIFY_ON_STARTUP,
    TEMP_DIR,
    runtime_label,
)
from database import get_db
from env_loader import load_environment


def prepare_runtime_directories() -> None:
    load_environment()
    os.makedirs(TEMP_DIR, exist_ok=True)


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


def _get_license_status() -> dict[str, any]:
    """Заглушка проверки лицензии — всегда возвращает активную лицензию."""
    hwid = get_hwid()
    
    # Если настроен сервер лицензий и включена проверка при старте
    if LICENSE_VERIFY_ON_STARTUP and LICENSE_SERVER_URL:
        try:
            from licensing.client import LicenseClient  # type: ignore[import-not-found]
            client = LicenseClient()
            
            status_obj = client.get_status(hwid)
            return {
                "active": True,
                "mode": "online",
                "message": None,
                "modules": [],
            } if getattr(status_obj, 'active', False) else {
                "active": False,
                "mode": "",
                "message": str(getattr(status_obj, 'message') or ""),
                "modules": [],
            }
        except Exception:
            pass
    
    return {
        "active": True,
        "mode": "offline",
        "message": None,
        "modules": [],
    }


def verify_license_at_boot() -> dict[str, any]:
    """Синхронная проверка лицензии при каждом запуске (кэш сбрасывается)."""
    db = get_db()
    
    status = _get_license_status()
    
    if status["active"]:
        msg = f"Лицензия OK ({status['mode']}), модули: {','.join(sorted(status.get('modules', []))) or '—'}"
        db.add_log("LIC-OK", msg)
    else:
        msg = status.get("message")
        if not msg:
            db.add_log("LIC-WARN", "Лицензия не активирована")
        else:
            db.add_log("LIC-WARN", msg)
    
    return status


def verify_license_at_boot_async() -> dict[str, any]:
    """Онлайн-валидация токена при старте, если настроен сервер лицензий."""
    if not LICENSE_VERIFY_ON_STARTUP:
        return _get_license_status()

    # Синхронная версия для совместимости с main.py
    status = verify_license_at_boot()
    
    if not LICENSE_SERVER_URL:
        return status
    
    try:
        from licensing.client import LicenseClient  # type: ignore[import-not-found]
        
        token = (get_db().get_setting("license_token", "") or "").strip()
        current_status = _get_license_status()
        
        if current_status["mode"] == "online" and token:
            try:
                client = LicenseClient()
                refreshed_obj = asyncio.run(client.refresh_online_if_needed(get_hwid(), force=True))  # type: ignore[attr-defined]
                
                new_active = getattr(refreshed_obj, 'active', False) if hasattr(refreshed_obj, 'active') else True
                
                return {
                    "active": new_active,
                    "mode": "online" if new_active else "",
                    "message": None if new_active else str(getattr(refreshed_obj, 'message') or ""),
                    "modules": [],
                }
            except Exception as exc:
                get_db().add_log("LIC-WARN", f"Онлайн-проверка при старте не удалась: {exc}")
        
        return status
        
    except ImportError:
        # licensing.client отсутствует — используем заглушку
        if not LICENSE_SERVER_URL or not token:
            return _get_license_status()
    
    return status


def run_startup_bootstrap() -> dict[str, any]:
    """Вызывается из main.py до запуска API и бота."""
    prepare_runtime_directories()
    seed_settings_from_env()
    print(f"--> [СИСТЕМА] Project MaryJane v{APP_VERSION} ({runtime_label()})")
    
    if LICENSE_VERIFY_ON_STARTUP:
        return verify_license_at_boot()
    
    return _get_license_status()


def run_startup_bootstrap_async() -> dict[str, any]:
    """Те же шаги, что run_startup_bootstrap, с онлайн-проверкой лицензии."""
    prepare_runtime_directories()
    seed_settings_from_env()
    print(f"--> [СИСТЕМА] Project MaryJane v{APP_VERSION} ({runtime_label()})")
    
    return asyncio.run(verify_license_at_boot_async())
