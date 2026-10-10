"""Интерактивное создание Telegram user-session для основного парсера.

Запускайте из терминала после сохранения tg_api_id и tg_api_hash в админ-панели:
    python tools/create_telegram_session.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import SESSION_PATH
from database import get_db
from telethon import TelegramClient


async def create_session() -> int:
    settings = get_db().get_all_settings()
    try:
        api_id = int((settings.get("tg_api_id") or "").strip())
    except (TypeError, ValueError):
        api_id = 0
    api_hash = (settings.get("tg_api_hash") or "").strip()

    if api_id <= 0 or not api_hash:
        print("Сначала заполните tg_api_id и tg_api_hash в настройках Telegram.")
        return 2

    client = TelegramClient(SESSION_PATH, api_id, api_hash)
    try:
        print("Войдите в Telegram в этом терминале. Код придёт от Telegram.")
        await client.start()
        me = await client.get_me()
        if me is None:
            print("Telegram не подтвердил вход. Повторите попытку.")
            return 1
        identity = getattr(me, "username", None) or f"ID {getattr(me, 'id', 'unknown')}"
        print(f"Сессия создана и авторизована ({identity}).")
        print(f"Файл сессии: {SESSION_PATH}.session")
        print("Не публикуйте этот файл и не передавайте его другим людям.")
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nВход отменён.")
        return 130
    except Exception as exc:
        print(f"Не удалось создать Telegram-сессию: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await client.disconnect()


def main() -> int:
    try:
        return asyncio.run(create_session())
    except KeyboardInterrupt:
        print("\nВход отменён.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
