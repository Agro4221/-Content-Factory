"""Проверка миграции секретов на копии реальной базы приложения.

Скрипт НЕ трогает рабочую базу: делает её копию во временной папке,
имитирует состояние «секреты сохранены открытым текстом» и проверяет,
что после запуска приложения секреты зашифрованы и всё ещё читаются.
"""
from pathlib import Path
import os
import shutil
import sqlite3
import sys
import tempfile

SRC_DB = str(Path(__file__).resolve().parents[1] / "bot_storage.db")
PROJECT = str(Path(__file__).resolve().parents[1])

sys.path.insert(0, PROJECT)

import secrets_store  # noqa: E402
from database import DB  # noqa: E402

FAKE_TOKEN = "123456789:AAFakeTokenForMigrationCheckNotARealToken"
FAKE_AI_KEY = "sk-fake-key-for-migration-check"
FAKE_PROXY_PASS = "fake-proxy-password"


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="cf_migration_")
    copy = os.path.join(tmpdir, "bot_storage.db")
    try:
        # Консистентная копия: в рабочей базе данные лежат в WAL-файле,
        # поэтому простое копирование .db дало бы пустую базу.
        src = sqlite3.connect(SRC_DB)
        dst = sqlite3.connect(copy)
        src.backup(dst)
        dst.close()
        src.close()
        print(f"Копия реальной БД: {copy}")

        # 1. Кладём секреты в копию открытым текстом (как было до шифрования).
        conn = sqlite3.connect(copy)
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            ("bot_token", FAKE_TOKEN),
        )
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            ("remote_api_key", FAKE_AI_KEY),
        )
        conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            ("proxy_pass", FAKE_PROXY_PASS),
        )
        conn.commit()
        conn.close()

        # 2. Проверяем: в файле секрет лежит открытым текстом.
        with open(copy, "rb") as fh:
            raw = fh.read()
        assert FAKE_TOKEN.encode() in raw, "до старта секрет должен лежать открытым текстом"
        print("ДО запуска: секрет в файле открытым текстом — подтверждено")

        # 3. Открываем базу так, как это делает приложение при старте.
        db = DB(copy)
        try:
            stored = db.conn.execute(
                "SELECT value FROM bot_settings WHERE key='bot_token'"
            ).fetchone()[0]
            assert secrets_store.is_encrypted(stored), "секрет не зашифрован"
            assert FAKE_TOKEN not in stored, "секрет остался читаемым в базе"
            print("ПОСЛЕ запуска: значение в базе зашифровано (префикс dpapi:v1:)")

            # 4. Приложение читает секреты как раньше — без повторного ввода.
            assert db.get_setting("bot_token") == FAKE_TOKEN
            assert db.get_setting("remote_api_key") == FAKE_AI_KEY
            assert db.get_setting("proxy_pass") == FAKE_PROXY_PASS
            print("ЧТЕНИЕ: секреты возвращаются в исходном виде — ручной ввод не нужен")

            # 5. В панель уходит маска, а не значение.
            masked = db.get_masked_settings()
            assert masked["bot_token"] != FAKE_TOKEN
            assert masked["bot_token"]
            assert FAKE_TOKEN not in str(masked)
            print("ПАНЕЛЬ: вместо значения возвращается маска")

            # 6. Повторное открытие базы ничего не ломает.
            db.conn.close()
            db2 = DB(copy)
            try:
                assert db2.get_setting("bot_token") == FAKE_TOKEN
                print("ПОВТОРНОЕ ОТКРЫТИЕ: секрет по-прежнему читается")
            finally:
                db2.conn.close()
        finally:
            try:
                db.conn.close()
            except Exception:
                pass

        # 7. Секрет не должен утечь в сам файл базы.
        with open(copy, "rb") as fh:
            raw_after = fh.read()
        assert FAKE_TOKEN.encode() not in raw_after
        assert FAKE_AI_KEY.encode() not in raw_after
        assert FAKE_PROXY_PASS.encode() not in raw_after
        print("ФАЙЛ БАЗЫ: открытых значений секретов не осталось")

        print("\nИТОГ: миграция на копии реальной базы прошла успешно.")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
