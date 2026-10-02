"""HWID и заглушка проверки лицензии (модуль licensing удалён)."""
from __future__ import annotations

import hashlib
import uuid

from config import LEGACY_LICENSE_SALT


def get_hwid() -> str:
    """Уникальный идентификатор машины для привязки лицензии."""
    mac = uuid.getnode()
    return hashlib.sha256(str(mac).encode()).hexdigest()[:32]


def check_license(module_id: str | None = None) -> bool:
    """
    Проверка лицензии для всего приложения или конкретного модуля.

    Всегда возвращает True, так как модули контроля лицензий удалены.
    """
    return True


def generate_valid_key(hwid: str | None = None) -> str:
    """Legacy HWID-ключ — заглушка после удаления оригинального генератора."""
    current_hwid = hwid or get_hwid()
    raw_key = f"{current_hwid}:{LEGACY_LICENSE_SALT}"
    return hashlib.sha256(raw_key.encode()).hexdigest()[:32]