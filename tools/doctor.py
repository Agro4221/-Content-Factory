"""Самопроверка окружения Content Factory.

Проверяет то, без чего парсер не запустится:

* установлены ли Python-пакеты (и доустанавливает недостающие);
* создаётся ли база данных и применяются ли миграции;
* создаются ли рабочие каталоги и есть ли к ним права;
* свободен ли порт панели управления;
* доступен ли механизм шифрования секретов.

Запуск:
    python tools/doctor.py            # только проверка
    python tools/doctor.py --install  # проверка + доустановка зависимостей
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import socket
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OK = "[ OK ]"
BAD = "[FAIL]"
WARN = "[WARN]"

_failures: list[str] = []
_warnings: list[str] = []


def report(status: str, title: str, detail: str = "") -> None:
    print(f"{status} {title}" + (f" — {detail}" if detail else ""))
    if status == BAD:
        _failures.append(title)
    elif status == WARN:
        _warnings.append(title)


# ---------- Зависимости ----------

def check_dependencies(install: bool) -> None:
    from config import REQUIRED_PACKAGES

    missing: list[str] = []
    for module_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            found = importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(pip_name)

    if not missing:
        report(OK, f"Зависимости установлены ({len(REQUIRED_PACKAGES)} пакетов)")
        return

    if not install:
        report(
            BAD,
            f"Не хватает зависимостей: {len(missing)}",
            ", ".join(missing) + " (запустите с --install)",
        )
        return

    print("--> [СИСТЕМА] Устанавливаем недостающие зависимости...")
    try:
        from deps import ensure_dependencies_installed

        ensure_dependencies_installed(missing)
    except Exception as exc:
        report(BAD, "Не удалось установить зависимости", f"{type(exc).__name__}: {exc}")
        return

    still_missing = [
        pip_name
        for module_name, pip_name in REQUIRED_PACKAGES.items()
        if importlib.util.find_spec(module_name) is None
    ]
    if still_missing:
        report(BAD, "Зависимости всё ещё отсутствуют", ", ".join(still_missing))
    else:
        report(OK, "Зависимости доустановлены")


# ---------- База данных ----------

def check_database() -> None:
    from config import DB_PATH

    try:
        conn = sqlite3.connect(DB_PATH)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        conn.close()
    except sqlite3.Error as exc:
        report(BAD, "База данных недоступна", f"{type(exc).__name__}: {exc}")
        return

    required = {"bot_settings", "system_logs", "published_posts", "skipped_posts"}
    missing = required - tables
    if missing:
        report(BAD, "В базе нет обязательных таблиц", ", ".join(sorted(missing)))
    else:
        report(OK, f"База данных готова (схема v{version}, путь: {DB_PATH})")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('__doctor__', '1')")
        conn.execute("DELETE FROM bot_settings WHERE key='__doctor__'")
        conn.commit()
        conn.close()
    except sqlite3.Error as exc:
        report(BAD, "Нет прав на запись в базу", str(exc))
        return
    report(OK, "База данных доступна для записи")


# ---------- Каталоги ----------

def check_directories() -> None:
    from config import BASE_DIR, QUEUE_MEDIA_DIR, SESSION_PATH, TEMP_DIR

    for label, path in (
        ("Каталог приложения", BASE_DIR),
        ("Временные файлы", TEMP_DIR),
        ("Медиа очереди", QUEUE_MEDIA_DIR),
    ):
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".doctor_write_test")
            with open(probe, "w", encoding="utf-8") as handle:
                handle.write("ok")
            os.remove(probe)
            report(OK, f"{label}: доступен для записи", path)
        except OSError as exc:
            report(BAD, f"{label}: нет доступа на запись", f"{path} — {exc}")

    session_file = SESSION_PATH + ".session"
    folder = os.path.dirname(session_file) or "."
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".doctor_write_test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        report(OK, "Папка сессии Telegram доступна для записи", folder)
    except OSError as exc:
        report(BAD, "Папка сессии Telegram недоступна", f"{folder} — {exc}")


# ---------- Порт панели ----------

def check_port() -> None:
    from config import API_HOST, API_PORT

    host = API_HOST if API_HOST else "127.0.0.1"
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((host, int(API_PORT)))
    except OSError as exc:
        report(
            WARN,
            f"Порт {API_PORT} уже занят",
            f"{exc}. Если Content Factory уже запущен — это нормально.",
        )
        return
    finally:
        probe.close()
    report(OK, f"Порт {API_PORT} свободен")


# ---------- Шифрование секретов ----------

def check_secret_storage() -> None:
    import secrets_store

    if not secrets_store.supported():
        report(
            WARN,
            "Шифрование секретов недоступно на этой платформе",
            "DPAPI работает только в Windows: секреты будут храниться открытым текстом.",
        )
        return
    try:
        sample = "doctor-check"
        encrypted = secrets_store.encrypt(sample)
        if not secrets_store.is_encrypted(encrypted):
            report(BAD, "Секреты не шифруются", "неверный формат значения")
            return
        if secrets_store.decrypt(encrypted) != sample:
            report(BAD, "Секреты не расшифровываются обратно")
            return
        report(OK, "Шифрование секретов работает (Windows DPAPI)")
    except Exception as exc:
        report(BAD, "Ошибка шифрования секретов", f"{type(exc).__name__}: {exc}")


# ---------- Панель управления ----------

def check_admin_assets() -> None:
    from config import ADMIN_HTML_PATH, BUNDLE_DIR

    for label, path in (
        ("Панель управления", ADMIN_HTML_PATH),
        ("Шаблон .env", os.path.join(BUNDLE_DIR, ".env.example")),
    ):
        if os.path.isfile(path):
            report(OK, f"{label} на месте", path)
        else:
            fallback = os.path.join(ROOT, os.path.basename(path))
            if os.path.isfile(fallback):
                report(OK, f"{label} на месте", fallback)
            else:
                report(BAD, f"{label} не найден", path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка окружения Content Factory")
    parser.add_argument(
        "--install",
        action="store_true",
        help="доустановить недостающие зависимости",
    )
    args = parser.parse_args()

    print("=" * 68)
    print("ПРОВЕРКА ОКРУЖЕНИЯ CONTENT FACTORY")
    print("=" * 68)
    print(f"Python: {sys.version.split()[0]} ({sys.platform})")
    print(f"Проект: {ROOT}")
    print()

    check_dependencies(args.install)
    check_database()
    check_directories()
    check_port()
    check_secret_storage()
    check_admin_assets()

    print()
    print("=" * 68)
    if _failures:
        print(f"ПРОБЛЕМЫ: {len(_failures)}")
        for item in _failures:
            print(f"  - {item}")
        print()
        print("Приложение не сможет работать, пока проблемы не устранены.")
        return 1
    if _warnings:
        print(f"ПРЕДУПРЕЖДЕНИЯ: {len(_warnings)}")
        for item in _warnings:
            print(f"  - {item}")
        print()
        print("Критичных проблем нет — приложение готово к работе.")
        return 0
    print("ВСЁ В ПОРЯДКЕ — приложение готово к работе.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
