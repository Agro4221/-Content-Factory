"""
SQLite-обёртка для проекта Content Factory.

Особенности:
  - Singleton через SharedDB: одна коннекция на всё приложение, не плодим
    PRAGMA-инициализации и не держим лишние хендлы.
  - WAL-режим + busy_timeout + threading.Lock: безопасная конкурентная запись
    из FastAPI-потока и asyncio-цикла бота.
  - Составной ключ поста (source:post_id) — иначе пост с одинаковым message.id
    из разных каналов-доноров считался бы одним и тем же.
  - **Версионированная схема через PRAGMA user_version + миграции.**
    Каждое изменение схемы — новая запись в _MIGRATIONS с номером версии.
    При открытии БД автоматически применяются миграции от текущей до
    последней версии. Идемпотентно, безопасно для отката к старой версии.
  - **Секреты шифруются** системным хранилищем ОС (см. secrets_store.py).
"""
from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from config import DB_PATH, SECRET_MASK, SECRET_SETTING_KEYS, make_post_key
from secrets_store import (
    SecretDecryptionError,
    SecretEncryptionError,
)
from secrets_store import decrypt as _decrypt_secret
from secrets_store import encrypt as _encrypt_secret
from secrets_store import has_dpapi_blob_signature as _has_dpapi_blob_signature
from secrets_store import supported as _secret_storage_supported


def _local_now_str() -> str:
    """Локальное время машины в формате SQLite DATETIME."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _local_datetime_str(delta_seconds: int = 0) -> str:
    """Локальное время с опциональным смещением (аналог datetime('now', '+N seconds'))."""
    return (datetime.now() + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%d %H:%M:%S")


def format_display_timestamp(ts: str | None) -> str:
    """Форматирует локальную метку времени для UI: HH:MM:SS DD.MM.YYYY."""
    if not ts:
        return "—"
    raw = str(ts).strip().replace("T", " ")[:19]
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return parsed.strftime("%H:%M:%S %d.%m.%Y")
    except ValueError:
        return str(ts)


def _anchor_sqlite_modifier(modifier: str) -> str:
    """Resolves SQLite 'start of day/month/year' modifiers in local time."""
    now = datetime.now()
    if modifier == "start of day":
        anchored = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif modifier == "start of month":
        anchored = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif modifier == "start of year":
        anchored = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"Unsupported SQLite anchor modifier: {modifier!r}")
    return anchored.strftime("%Y-%m-%d %H:%M:%S")

def _parse_sqlite_modifier(modifier: str) -> int:
    """Разбирает '+24 hours', '-7 days', '+300 seconds' в смещение в секундах."""
    raw = modifier.strip()
    sign = -1 if raw.startswith("-") else 1
    body = raw.lstrip("+-").strip()
    parts = body.split()
    if len(parts) == 1:
        return sign * int(parts[0])
    value = int(parts[0])
    unit = parts[1].lower().rstrip("s")
    multipliers = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
    }
    return sign * value * multipliers.get(unit, 1)


_logger = logging.getLogger("database")
if not _logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[DB] %(levelname)s: %(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)


# === СИСТЕМА МИГРАЦИЙ ===
# Каждая миграция — функция, принимающая DB и применяющая изменения.
# Номер версии = ключ, монотонно растёт. Чтобы добавить миграцию:
#   1. Дописать (VERSION, apply_fn) в конец списка.
#   2. Никогда не редактировать уже применённые миграции.
#   3. Никогда не понижать CURRENT_SCHEMA_VERSION.
#
# Миграция получает db и сама выполняет свои SQL/изменения. Должна быть
# идемпотентной (повторный запуск не ломает БД) — на случай если процесс
# упал посреди применения.
MIGRATIONS: list[tuple[int, Callable[[DB], None]]] = []


def _migration_v1(db: DB) -> None:
    """v1: начальная схема — все 4 таблицы, дефолтные настройки, составной ключ."""
    db._create_initial_tables()
    db._init_default_settings()


def _migration_v2(db: DB) -> None:
    """v2: created_at + индексы для дашбордов.

    Добавляет в published_posts и skipped_posts колонку created_at
    (если её ещё нет) и создаёт индексы под типовые аналитические
    запросы модульных дашбордов. Полностью идемпотентно — каждый
    шаг проверяет состояние через PRAGMA / IF NOT EXISTS.
    """
    # 1) created_at в published_posts
    cols_pub = [r[1] for r in db.conn.execute(
        "PRAGMA table_info(published_posts)").fetchall()]
    if "created_at" not in cols_pub:
        with db.conn:
            db.conn.execute(
                "ALTER TABLE published_posts ADD COLUMN created_at "
                "DATETIME DEFAULT CURRENT_TIMESTAMP"
            )

    # 2) created_at в skipped_posts
    cols_skip = [r[1] for r in db.conn.execute(
        "PRAGMA table_info(skipped_posts)").fetchall()]
    if "created_at" not in cols_skip:
        with db.conn:
            db.conn.execute(
                "ALTER TABLE skipped_posts ADD COLUMN created_at "
                "DATETIME DEFAULT CURRENT_TIMESTAMP"
            )

    # 3) Индексы. CREATE INDEX IF NOT EXISTS — идемпотентно на уровне SQLite.
    indexes = (
        "CREATE INDEX IF NOT EXISTS idx_published_created_at ON published_posts(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_published_source_created_at ON published_posts(source_name, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_skipped_created_at ON skipped_posts(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_skipped_reason ON skipped_posts(reason)",
        "CREATE INDEX IF NOT EXISTS idx_logs_type_timestamp ON system_logs(type, timestamp)",
    )
    with db.conn:
        for stmt in indexes:
            db.conn.execute(stmt)

    # 4) Backfill: для строк без created_at проставляем NOW(),
    # чтобы счётчики "сегодня" / "24h" не врали на старых БД.
    with db.conn:
        db.conn.execute(
            "UPDATE published_posts SET created_at = CURRENT_TIMESTAMP "
            "WHERE created_at IS NULL"
        )
        db.conn.execute(
            "UPDATE skipped_posts SET created_at = CURRENT_TIMESTAMP "
            "WHERE created_at IS NULL"
        )


def _migration_v3(db: DB) -> None:
    """Номер версии сохранён для совместимости с существующими базами данных."""
    return


def _migration_v4(db: DB) -> None:
    """Структура базы данных не меняется; номер версии сохранён для совместимости."""
    return


def _migration_v5(db: DB) -> None:
    """v5: старые system_logs были в UTC (CURRENT_TIMESTAMP) — переводим в локальное."""
    if db.get_setting("logs_utc_to_local_done") == "1":
        return
    with db.conn:
        db.conn.execute("DELETE FROM system_logs WHERE type = 'TST'")
        db.conn.execute(
            "UPDATE system_logs SET timestamp = datetime(timestamp, 'localtime') "
            "WHERE timestamp IS NOT NULL"
        )
    db.set_setting("logs_utc_to_local_done", "1")


def _migration_v6(db: DB) -> None:
    """Переводит старые временные метки публикаций к локальному времени для статистики."""
    if db._get_schema_version() >= 6:
        return

    # Explicit SQL transaction; the schema version commits with the timestamp
    # update so an interrupted startup can never shift the same rows twice.
    db.conn.execute("BEGIN IMMEDIATE")
    try:
        for table in ("published_posts", "skipped_posts"):
            columns = {
                row[1]
                for row in db.conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "created_at" not in columns:
                continue
            db.conn.execute(
                f"UPDATE {table} SET created_at = "
                "CASE WHEN datetime(created_at) IS NOT NULL "
                "THEN datetime(created_at, 'localtime') ELSE created_at END "
                "WHERE created_at IS NOT NULL"
            )
        db.conn.execute("PRAGMA user_version = 6")
        db.conn.commit()
    except BaseException:
        if db.conn.in_transaction:
            db.conn.rollback()
        raise


def _migration_v7(db: DB) -> None:
    """v7: durable retry queue for caption text sent after media."""
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_text_followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            post_id TEXT NOT NULL,
            target_chat TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_attempt_at TEXT NOT NULL,
            UNIQUE(source_name, post_id, target_chat)
        )
        """
    )
    db.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_followups_due "
        "ON pending_text_followups(next_attempt_at, id)"
    )


def _migration_v8(db: DB) -> None:
    """v8: очередь постов для ручного контроля, отложенной публикации и правок."""
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS post_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            post_id TEXT NOT NULL,
            group_id TEXT DEFAULT '',
            original_text TEXT NOT NULL DEFAULT '',
            publish_text TEXT NOT NULL DEFAULT '',
            media_files TEXT NOT NULL DEFAULT '[]',
            media_kind TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            target_chat TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            scheduled_at TEXT,
            published_at TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_name, post_id)
        )
        """
    )
    db.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_post_queue_status "
        "ON post_queue(status, scheduled_at, id)"
    )
    db.conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_post_queue_source "
        "ON post_queue(source_name, post_id)"
    )


def _migration_v9(db: DB) -> None:
    """v9: список целевых каналов для поста.

    Один пост может уйти сразу в несколько каналов: теги совпали с
    маршрутами нескольких каналов. Старая колонка target_chat остаётся
    основным каналом — по ней видно, куда пост ушёл.
    """
    columns = {
        row[1]
        for row in db.conn.execute("PRAGMA table_info(post_queue)").fetchall()
    }
    if not columns:
        return
    if "target_chats" not in columns:
        db.conn.execute(
            "ALTER TABLE post_queue ADD COLUMN target_chats TEXT NOT NULL DEFAULT '[]'"
        )
    # Для старых записей список каналов совпадает с основным каналом.
    db.conn.execute(
        "UPDATE post_queue SET target_chats = '[]' "
        "WHERE target_chats IS NULL OR target_chats = ''"
    )


def _migration_v10(db: DB) -> None:
    """v10: счётчик попыток публикации для постов очереди.

    Пост, который не публикуется из-за постоянной ошибки, не должен
    повторяться вечно: после нескольких попыток он переводится в
    «ошибка» и перестаёт занимать очередь.
    """
    columns = {
        row[1]
        for row in db.conn.execute("PRAGMA table_info(post_queue)").fetchall()
    }
    if not columns:
        return
    if "publish_attempts" not in columns:
        db.conn.execute(
            "ALTER TABLE post_queue ADD COLUMN publish_attempts INTEGER NOT NULL DEFAULT 0"
        )


# Регистрируем миграции
MIGRATIONS.append((1, _migration_v1))
MIGRATIONS.append((2, _migration_v2))
MIGRATIONS.append((3, _migration_v3))
MIGRATIONS.append((4, _migration_v4))
MIGRATIONS.append((5, _migration_v5))
MIGRATIONS.append((6, _migration_v6))
MIGRATIONS.append((7, _migration_v7))
MIGRATIONS.append((8, _migration_v8))
MIGRATIONS.append((9, _migration_v9))
MIGRATIONS.append((10, _migration_v10))

CURRENT_SCHEMA_VERSION: int = max(v for v, _ in MIGRATIONS)


# Дефолты вынесены в константу — DRY и легче расширять.
# Только то, что бот использует прямо сейчас.
_DEFAULT_SETTINGS: dict[str, str] = {
    # --- Telegram (глобально) ---
    "tg_api_id": "",
    "tg_api_hash": "",
    "bot_token": "",

    # --- AI ---
    "ai_mode": "LOCAL",
    "local_ai_provider": "",
    "local_host": "127.0.0.1",
    "local_port": "1234",
    "local_model": "google/gemma-3-4b",
    "remote_url": "https://openrouter.ai/api/v1/chat/completions",
    "remote_api_key": "",
    "remote_model": "google/gemma-3-27b",
    "system_prompt": "",

    # --- Парсинг ---
    "sources": "",
    "topic_channels": "{}",
    "ads_words": "",
    "watermark_text": "",

    "poll_delay": "60",

    # --- Прокси (общий) ---
    "use_proxy": "0",
    "proxy_type": "HTTP",
    "proxy_host": "",
    "proxy_port": "",
    "proxy_user": "",
    "proxy_pass": "",
    "mtproto_key": "",

    # --- Публикация ---
    "publish_mode": "auto",
    "media_retention_days": "3",
    "media_cleanup_enabled": "1",
}


class DatabaseWriteError(RuntimeError):
    """A database write failed after a post may already have been published."""


class DB:
    """Потокобезопасная (через WAL+busy_timeout+threading.Lock) обёртка над SQLite.

    BUG-014: при выключенном GIL (Py3.13t) или в простом многопоточном сценарии
    несколько потоков одновременно дёргают self.conn.execute(...) — это
    приводит к DatabaseError('file is not a database') из sqlite3, потому что
    объекты Connection не thread-safe на уровне Python. WAL и busy_timeout
    спасают от уровня блокировок SQLite, но не от race на самом объекте.

    Все методы get_/set_/add_ обёрнуты в self._lock — простой threading.Lock.
    Блокировка держится только на время одного метода (не транзакции),
    так что overhead минимален.

    Версионирование схемы: PRAGMA user_version хранит текущую версию.
    При конструировании БД все миграции ниже текущей версии применяются
    автоматически. Это безопасно для существующих БД.
    """

    def __init__(self, db_path: str = DB_PATH) -> None:
        import threading
        self._lock = threading.Lock()
        self.conn: sqlite3.Connection = sqlite3.connect(
            db_path,
            check_same_thread=False,
            timeout=10,
            isolation_level=None,  # autocommit; транзакции — через `with`
        )
        self._configure_pragmas()
        # Refuse a database from a newer application before any helper attempts
        # schema repair/backfill; even "helpful" ALTERs would mutate an unsupported DB.
        schema_version = self._get_schema_version()
        if schema_version > CURRENT_SCHEMA_VERSION:
            self.conn.close()
            raise RuntimeError(
                f"Схема БД (v{schema_version}) новее поддерживаемой "
                f"(v{CURRENT_SCHEMA_VERSION}). Обновите код или используйте "
                "бэкап БД старой версии."
            )
        self._ensure_created_at_columns()
        self._run_migrations()
        self._migrate_legacy_post_keys()  # для очень старых БД до версионирования
        # Секреты, сохранённые до включения шифрования, переводятся в
        # зашифрованный вид. Значения не выводятся никуда.
        self.migrate_plaintext_secrets()
        self._warn_if_secret_storage_unavailable()

    # ---------- init ----------
    def _configure_pragmas(self) -> None:
        with self.conn:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.execute("PRAGMA foreign_keys=ON")

    def _ensure_created_at_columns(self) -> None:
        """Добавляет временные метки до запуска миграций над заполненными таблицами.

        SQLite может отклонить ALTER TABLE ... DEFAULT CURRENT_TIMESTAMP,
        если таблица уже содержит строки. Сначала добавляем столбец без
        значения по умолчанию; заполнение меток и создание индексов можно повторять.
        """
        table_indexes = {
            "published_posts": (
                "CREATE INDEX IF NOT EXISTS idx_published_created_at ON published_posts(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_published_source_created_at ON published_posts(source_name, created_at)",
            ),
            "skipped_posts": (
                "CREATE INDEX IF NOT EXISTS idx_skipped_created_at ON skipped_posts(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_skipped_reason ON skipped_posts(reason)",
            ),
        }
        schema_version = self._get_schema_version()
        for table, indexes in table_indexes.items():
            columns = [
                row[1]
                for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            if not columns:
                continue
            with self.conn:
                if "created_at" not in columns:
                    self.conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN created_at DATETIME"
                    )
                # Before v6, timestamps are normalized by migration v6. Backfill
                # with the same UTC convention as SQLite CURRENT_TIMESTAMP so
                # the migration can convert the values exactly once.
                if schema_version < 6:
                    self.conn.execute(
                        f"UPDATE {table} SET created_at = CURRENT_TIMESTAMP "
                        "WHERE created_at IS NULL"
                    )
                else:
                    self.conn.execute(
                        f"UPDATE {table} SET created_at = ? WHERE created_at IS NULL",
                        (_local_now_str(),),
                    )
                for statement in indexes:
                    self.conn.execute(statement)

    def _get_schema_version(self) -> int:
        """Читает PRAGMA user_version. 0 для свежей БД."""
        try:
            row = self.conn.execute("PRAGMA user_version").fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    def _set_schema_version(self, version: int) -> None:
        """Записывает PRAGMA user_version. SQLite хранит как 32-bit int."""
        with self.conn:
            self.conn.execute(f"PRAGMA user_version = {int(version)}")

    def _run_migrations(self) -> None:
        """Применяет миграции от текущей версии до CURRENT_SCHEMA_VERSION.

        Идемпотентно: повторный запуск не ломает БД.
        Защита от битых миграций: если user_version > CURRENT_SCHEMA_VERSION,
        значит БД создана более новой версией кода → отказываемся работать.
        """
        current = self._get_schema_version()
        if current > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Схема БД (v{current}) новее поддерживаемой (v{CURRENT_SCHEMA_VERSION}). "
                f"Обновите код или используйте бэкап БД старой версии."
            )
        if current == CURRENT_SCHEMA_VERSION:
            return  # Ничего не делаем

        # Применяем миграции в порядке возрастания версии
        for version, migration_fn in sorted(MIGRATIONS, key=lambda x: x[0]):
            if version <= current:
                continue
            try:
                with self.conn:  # Транзакция — атомарно
                    migration_fn(self)
                self._set_schema_version(version)
                _logger.info("[DB] Applied migration v%d", version)
            except sqlite3.Error:
                # Не получилось — БД остаётся на старой версии, при следующем
                # запуске миграция повторится.
                _logger.exception("[DB] Migration v%d failed", version)
                raise

    def _create_initial_tables(self) -> None:
        """Создаёт начальный набор таблиц. Вызывается из миграции v1."""
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS published_posts (
                    post_key TEXT PRIMARY KEY,
                    post_id TEXT,
                    source_name TEXT
                )
                """
            )
            # post_key объединяет источник и ID сообщения; post_id хранит исходный ID.
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skipped_posts (
                    post_key TEXT PRIMARY KEY,
                    post_id TEXT,
                    source_name TEXT,
                    reason TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    type TEXT,
                    message TEXT
                )
                """
            )

    def _migrate_legacy_post_keys(self) -> None:
        """
        Для БД, созданных до composite-key: переносим данные в новый формат.
        Идемпотентно — если таблица уже новая, миграция no-op.
        """
        try:
            cols = [row[1] for row in self.conn.execute("PRAGMA table_info(published_posts)").fetchall()]
        except sqlite3.Error as exc:
            _logger.exception("Could not inspect published_posts before legacy migration")
            raise RuntimeError(
                "Cannot inspect the legacy published_posts schema; database startup aborted."
            ) from exc
        if "post_key" not in cols:
            try:
                with self.conn:
                    # DB uses isolation_level=None (autocommit), so explicitly
                    # begin a transaction for this multi-statement schema change.
                    self.conn.execute("BEGIN IMMEDIATE")
                    # Копируем старые данные с переименованием в новый формат
                    self.conn.execute("ALTER TABLE published_posts RENAME TO _published_posts_legacy")
                    self.conn.execute(
                        """
                        CREATE TABLE published_posts (
                            post_key TEXT PRIMARY KEY,
                            post_id TEXT,
                            source_name TEXT,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                    if "created_at" in cols:
                        self.conn.execute(
                            """
                            INSERT OR IGNORE INTO published_posts (
                                post_key, post_id, source_name, created_at
                            )
                            SELECT (source_name || ':' || post_id), post_id, source_name, created_at
                            FROM _published_posts_legacy
                            """
                        )
                    else:
                        self.conn.execute(
                            """
                            INSERT OR IGNORE INTO published_posts (post_key, post_id, source_name)
                            SELECT (source_name || ':' || post_id), post_id, source_name
                            FROM _published_posts_legacy
                            """
                        )
                    self.conn.execute("DROP TABLE _published_posts_legacy")
                    self.conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_published_created_at "
                        "ON published_posts(created_at)"
                    )
                    self.conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_published_source_created_at "
                        "ON published_posts(source_name, created_at)"
                    )
                    _logger.info("Migrated published_posts to composite key")
            except sqlite3.Error as e:
                _logger.exception("Migration of published_posts failed; aborting startup")
                raise RuntimeError(
                    "Legacy published_posts migration failed; refusing to run with an incompatible database."
                ) from e

        # Та же история для skipped_posts — на случай, если в БД она была создана
        # ещё до моего фикса (там post_id как PK).
        try:
            cols = [row[1] for row in self.conn.execute("PRAGMA table_info(skipped_posts)").fetchall()]
        except sqlite3.Error as exc:
            _logger.exception("Could not inspect skipped_posts before legacy migration")
            raise RuntimeError(
                "Cannot inspect the legacy skipped_posts schema; database startup aborted."
            ) from exc
        if cols and "post_key" not in cols:
            try:
                with self.conn:
                    # Keep the rename/create/copy/drop sequence atomic in autocommit mode.
                    self.conn.execute("BEGIN IMMEDIATE")
                    self.conn.execute("ALTER TABLE skipped_posts RENAME TO _skipped_posts_legacy")
                    self.conn.execute(
                        """
                        CREATE TABLE skipped_posts (
                            post_key TEXT PRIMARY KEY,
                            post_id TEXT,
                            source_name TEXT,
                            reason TEXT,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                    if "created_at" in cols:
                        self.conn.execute(
                            """
                            INSERT OR IGNORE INTO skipped_posts (
                                post_key, post_id, source_name, reason, created_at
                            )
                            SELECT (source_name || ':' || post_id), post_id, source_name, reason, created_at
                            FROM _skipped_posts_legacy
                            """
                        )
                    else:
                        self.conn.execute(
                            """
                            INSERT OR IGNORE INTO skipped_posts (post_key, post_id, source_name, reason)
                            SELECT (source_name || ':' || post_id), post_id, source_name, reason
                            FROM _skipped_posts_legacy
                            """
                        )
                    self.conn.execute("DROP TABLE _skipped_posts_legacy")
                    self.conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_skipped_created_at "
                        "ON skipped_posts(created_at)"
                    )
                    self.conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_skipped_reason "
                        "ON skipped_posts(reason)"
                    )
                    _logger.info("Migrated skipped_posts to composite key")
            except sqlite3.Error as e:
                _logger.exception("Migration of skipped_posts failed; aborting startup")
                raise RuntimeError(
                    "Legacy skipped_posts migration failed; refusing to run with an incompatible database."
                ) from e

    def _init_default_settings(self) -> None:
        with self.conn:
            for k, v in _DEFAULT_SETTINGS.items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
                    (k, v),
                )

    def _warn_if_secret_storage_unavailable(self) -> None:
        """Сообщает, если секреты не удаётся шифровать на этой платформе."""
        if _secret_storage_supported():
            return
        with contextlib.suppress(Exception):
            self.add_log(
                "WARN-503",
                "Защищённое хранение секретов недоступно. "
                "Новые значения не будут сохранены; проверьте целевую Windows-среду.",
            )

    # ---------- settings ----------
    def _decode_setting(self, key: str, value: Any) -> Any:
        """Расшифровывает значение секретной настройки.

        Значения, сохранённые до включения шифрования (legacy-plaintext),
        возвращаются без изменений. Если блоб не расшифровывается (база
        перенесена на другой ПК или другой пользователь Windows), значение
        считается пустым, а в журнал пишется ошибка без значения секрета.
        """
        if key not in SECRET_SETTING_KEYS or not isinstance(value, str):
            return value
        if not value:
            return value
        try:
            return _decrypt_secret(value, key)
        except SecretDecryptionError as exc:
            self._log_secret_failure("ERR-502", key, exc)
            return ""

    def _encode_setting(self, key: str, value: Any) -> str:
        """Encrypt a secret before writing it; never store the original on failure."""
        text = "" if value is None else str(value)
        if key not in SECRET_SETTING_KEYS or not text:
            return text

        if not _secret_storage_supported():
            exc = SecretEncryptionError(
                "Защищённое хранение секретов доступно только в целевой Windows-среде."
            )
            self._log_secret_failure("ERR-503", key, exc)
            raise exc

        try:
            return _encrypt_secret(text, key)
        except SecretEncryptionError as exc:
            self._log_secret_failure("ERR-503", key, exc)
            raise SecretEncryptionError(
                "Не удалось зашифровать секрет. Значение не сохранено."
            ) from exc

    def _log_secret_failure(self, log_type: str, key: str, exc: Exception) -> None:
        """Пишет в журнал факт проблемы с секретом, не раскрывая его значение."""
        with contextlib.suppress(Exception):
            self.add_log(
                log_type,
                f"Настройка «{key}»: не удалось обработать сохранённый секрет "
                f"({type(exc).__name__}). Введите значение заново в панели управления.",
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT value FROM bot_settings WHERE key=?", (key,))
            row = cur.fetchone()
            value = row[0] if row else default
        return self._decode_setting(key, value)

    def set_setting(self, key: str, value: Any) -> None:
        stored = self._encode_setting(key, value)
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
                (key, stored),
            )

    def get_all_settings(self) -> dict[str, str]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT key, value FROM bot_settings")
            rows = cur.fetchall()
        return {row[0]: self._decode_setting(row[0], row[1]) for row in rows}

    def get_masked_settings(self) -> dict[str, str]:
        """Настройки для интерфейса: секреты заменены маской.

        Значения секретов не покидают бэкенд в открытом виде — панель видит
        только признак «сохранено» и может заменить значение или удалить его.
        """
        settings = self.get_all_settings()
        for key in SECRET_SETTING_KEYS:
            if key in settings:
                settings[key] = "" if not settings.get(key) else SECRET_MASK
        return settings

    def set_settings(self, settings: dict[str, Any]) -> None:
        """Persist a batch of settings atomically or roll the entire batch back."""
        if not settings:
            return
        rows = [
            (str(key), self._encode_setting(str(key), value))
            for key, value in settings.items()
        ]
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.executemany(
                    "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
                    rows,
                )
                self.conn.commit()
            except sqlite3.Error as exc:
                try:
                    if self.conn.in_transaction:
                        self.conn.rollback()
                except sqlite3.Error:
                    pass
                _logger.exception("set_settings batch failed (%d keys)", len(rows))
                raise RuntimeError("could not save settings batch; transaction rolled back") from exc

    def migrate_plaintext_secrets(self) -> int:
        """Переводит ранее сохранённые plaintext-секреты в зашифрованный вид.

        Возвращает количество переведённых настроек. Значения секретов не
        выводятся ни в журнал, ни в терминал.
        """
        if not _secret_storage_supported():
            return 0
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT key, value FROM bot_settings")
            rows = cur.fetchall()

        migrated = 0
        for key, value in rows:
            if key not in SECRET_SETTING_KEYS:
                continue
            if not value or _has_dpapi_blob_signature(value):
                continue
            # Префикс и корректная Base64-строка сами по себе не означают,
            # что значение является DPAPI-блобом. Обычный текст в таком виде
            # тоже нужно зашифровать при миграции.
            try:
                encrypted = _encrypt_secret(value, key)
            except SecretEncryptionError as exc:
                self._log_secret_failure("ERR-503", key, exc)
                continue
            with self._lock, self.conn:
                self.conn.execute(
                    "UPDATE bot_settings SET value = ? WHERE key = ?",
                    (encrypted, key),
                )
            migrated += 1
        if migrated:
            _logger.info("[DB] Encrypted %d stored secret(s) with Windows DPAPI", migrated)
            self._purge_free_pages()
        return migrated

    def _purge_free_pages(self) -> None:
        """Пересобирает файл базы, чтобы старые страницы со значениями исчезли.

        SQLite не обнуляет освобождённые страницы: после перезаписи секрета
        его открытый текст может остаться в неиспользуемых страницах файла.
        VACUUM пересобирает базу и удаляет их.
        """
        try:
            with self._lock:
                self.conn.execute("VACUUM")
            _logger.info("[DB] Rebuilt database file to purge replaced secret pages")
        except sqlite3.Error as exc:
            # Не критично: значения в базе уже зашифрованы, остаётся лишь
            # возможный хвост в свободных страницах файла.
            _logger.warning("[DB] VACUUM skipped: %s", exc)

    # ---------- posts ----------
    def is_published(self, source: str, post_id: int | str) -> bool:
        with self._lock:
            return self._exists("published_posts", make_post_key(source, post_id))

    def is_skipped(self, source: str, post_id: int | str) -> bool:
        with self._lock:
            return self._exists("skipped_posts", make_post_key(source, post_id))

    def _exists(self, table: str, post_key: str) -> bool:
        cur = self.conn.cursor()
        cur.execute(f"SELECT 1 FROM {table} WHERE post_key=?", (post_key,))
        return cur.fetchone() is not None

    def save_post(self, source: str, post_id: int | str) -> None:
        """Record a successful publication, raising if SQLite cannot persist it."""
        post_key = make_post_key(source, post_id)
        values = (post_key, str(post_id), source, _local_now_str())
        with self._lock:
            try:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO published_posts (post_key, post_id, source_name, created_at) VALUES (?, ?, ?, ?)",
                        values,
                    )
            except sqlite3.IntegrityError as exc:
                # A duplicate key is expected only when this post is already recorded.
                try:
                    existing = self.conn.execute(
                        "SELECT 1 FROM published_posts WHERE post_key = ?",
                        (post_key,),
                    ).fetchone()
                except sqlite3.Error as check_error:
                    _logger.exception(
                        "save_post failed for %s/%s while checking duplicate",
                        source,
                        post_id,
                    )
                    raise DatabaseWriteError(
                        f"could not verify publication record for {source}/{post_id}"
                    ) from check_error
                if existing:
                    return
                _logger.exception("save_post integrity failure for %s/%s", source, post_id)
                raise DatabaseWriteError(
                    f"could not record publication for {source}/{post_id}"
                ) from exc
            except sqlite3.Error as exc:
                _logger.exception("save_post failed for %s/%s", source, post_id)
                raise DatabaseWriteError(
                    f"could not record publication for {source}/{post_id}"
                ) from exc

    def save_posts(self, source: str, post_ids: list[int | str]) -> None:
        """Atomically persist all IDs in one already-published Telegram message group.

        Telegram sends an album as one logical post. If only some of its source
        IDs were recorded before a SQLite error, a retry could resend the entire
        album and duplicate media already delivered. The group records must commit
        together or not at all.
        """
        if not post_ids:
            return

        now = _local_now_str()
        rows = [
            (make_post_key(source, post_id), str(post_id), source, now)
            for post_id in post_ids
        ]
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.executemany(
                    """
                    INSERT INTO published_posts (post_key, post_id, source_name, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(post_key) DO NOTHING
                    """,
                    rows,
                )
                self.conn.commit()
            except sqlite3.Error as exc:
                try:
                    if self.conn.in_transaction:
                        self.conn.rollback()
                except sqlite3.Error:
                    pass
                _logger.exception(
                    "save_posts failed for group %s (%s IDs)",
                    source,
                    len(post_ids),
                )
                raise DatabaseWriteError(
                    f"could not record publication group for {source}"
                ) from exc

    def save_posts_with_followup(
        self,
        source: str,
        post_ids: list[int | str],
        target_chat: str,
        text: str,
    ) -> None:
        """Atomically record a delivered media post and its undelivered text tail.

        The media has already been accepted by Telegram before this method is
        called. Committing the deduplication markers and tail queue together
        prevents a restart from resending the media while losing its tail.
        """
        if not post_ids:
            return
        if not target_chat or not text:
            raise ValueError("target_chat and follow-up text must be non-empty")

        now = _local_now_str()
        rows = [
            (make_post_key(source, post_id), str(post_id), source, now)
            for post_id in post_ids
        ]
        followup = (
            source,
            str(post_ids[0]),
            target_chat,
            text,
            now,
            now,
        )
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.executemany(
                    """
                    INSERT INTO published_posts (post_key, post_id, source_name, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(post_key) DO NOTHING
                    """,
                    rows,
                )
                self.conn.execute(
                    """
                    INSERT INTO pending_text_followups
                        (source_name, post_id, target_chat, text, created_at, next_attempt_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_name, post_id, target_chat) DO NOTHING
                    """,
                    followup,
                )
                self.conn.commit()
            except sqlite3.Error as exc:
                try:
                    if self.conn.in_transaction:
                        self.conn.rollback()
                except sqlite3.Error:
                    pass
                _logger.exception(
                    "save_posts_with_followup failed for %s/%s",
                    source,
                    post_ids[0],
                )
                raise DatabaseWriteError(
                    f"could not atomically record publication and text follow-up for {source}/{post_ids[0]}"
                ) from exc

    def get_due_pending_text_followups(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return follow-up messages whose persistent retry deadline has passed."""
        bounded_limit = max(1, min(int(limit), 100))
        now = _local_now_str()
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id, source_name, post_id, target_chat, text, attempts
                FROM pending_text_followups
                WHERE next_attempt_at <= ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (now, bounded_limit),
            ).fetchall()
        return [
            {
                "id": int(row[0]),
                "source_name": str(row[1]),
                "post_id": str(row[2]),
                "target_chat": str(row[3]),
                "text": str(row[4]),
                "attempts": int(row[5]),
            }
            for row in rows
        ]

    def mark_pending_text_followup_sent(self, followup_id: int) -> None:
        """Remove a follow-up from the durable queue after Telegram accepts it."""
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.execute(
                    "DELETE FROM pending_text_followups WHERE id = ?",
                    (int(followup_id),),
                )
                self.conn.commit()
            except sqlite3.Error as exc:
                try:
                    if self.conn.in_transaction:
                        self.conn.rollback()
                except sqlite3.Error:
                    pass
                raise DatabaseWriteError(
                    f"could not acknowledge text follow-up {followup_id}"
                ) from exc

    def record_pending_text_followup_failure(
        self,
        followup_id: int,
        error: str,
        retry_after_seconds: int | None = None,
    ) -> None:
        """Keep failed text tails queued with exponential backoff."""
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                row = self.conn.execute(
                    "SELECT attempts FROM pending_text_followups WHERE id = ?",
                    (int(followup_id),),
                ).fetchone()
                if row is None:
                    self.conn.commit()
                    return
                attempts = int(row[0]) + 1
                if retry_after_seconds is not None:
                    delay = max(1, min(int(retry_after_seconds), 86400))
                else:
                    delay = min(30 * (2 ** min(attempts - 1, 6)), 1800)
                self.conn.execute(
                    """
                    UPDATE pending_text_followups
                    SET attempts = ?, last_error = ?, next_attempt_at = ?
                    WHERE id = ?
                    """,
                    (attempts, str(error)[:500], _local_datetime_str(delay), int(followup_id)),
                )
                self.conn.commit()
            except (sqlite3.Error, TypeError, ValueError) as exc:
                try:
                    if self.conn.in_transaction:
                        self.conn.rollback()
                except sqlite3.Error:
                    pass
                raise DatabaseWriteError(
                    f"could not schedule retry for text follow-up {followup_id}"
                ) from exc

    def save_skipped_posts(
        self,
        source: str,
        post_ids: list[int | str],
        reason: str,
    ) -> None:
        """Record a whole skipped Telegram group atomically or roll it all back."""
        if not post_ids:
            return

        now = _local_now_str()
        rows = [
            (make_post_key(source, post_id), str(post_id), source, reason, now)
            for post_id in post_ids
        ]
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.executemany(
                    """
                    INSERT INTO skipped_posts
                        (post_key, post_id, source_name, reason, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(post_key) DO NOTHING
                    """,
                    rows,
                )
                self.conn.commit()
            except sqlite3.Error as exc:
                try:
                    if self.conn.in_transaction:
                        self.conn.rollback()
                except sqlite3.Error:
                    pass
                _logger.exception(
                    "save_skipped_posts failed for group %s (%s IDs)",
                    source,
                    len(post_ids),
                )
                raise DatabaseWriteError(
                    f"could not record skipped group for {source}"
                ) from exc

    def save_skipped_post(self, source: str, post_id: int | str, reason: str) -> None:
        """Помечает пост как пропущенный (реклама/пустой)."""
        with self._lock:
            try:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO skipped_posts (post_key, post_id, source_name, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                        (make_post_key(source, post_id), str(post_id), source, reason, _local_now_str()),
                    )
            except sqlite3.IntegrityError as exc:
                try:
                    existing = self.conn.execute(
                        "SELECT 1 FROM skipped_posts WHERE post_key = ?",
                        (make_post_key(source, post_id),),
                    ).fetchone()
                except sqlite3.Error as check_error:
                    _logger.exception(
                        "save_skipped_post failed for %s/%s while checking duplicate",
                        source,
                        post_id,
                    )
                    raise DatabaseWriteError(
                        f"could not verify skipped record for {source}/{post_id}"
                    ) from check_error
                if existing:
                    return
                _logger.exception(
                    "save_skipped_post integrity failure for %s/%s",
                    source,
                    post_id,
                )
                raise DatabaseWriteError(
                    f"could not record skipped post for {source}/{post_id}"
                ) from exc
            except sqlite3.Error as exc:
                _logger.exception(
                    "save_skipped_post failed for %s/%s",
                    source,
                    post_id,
                )
                raise DatabaseWriteError(
                    f"could not record skipped post for {source}/{post_id}"
                ) from exc

    # ---------- logs ----------
    def add_log(self, log_type: str, message: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO system_logs (timestamp, type, message) VALUES (?, ?, ?)",
                (_local_now_str(), log_type, message),
            )

    def get_logs(self, limit: int = 30, offset: int = 0) -> list[tuple[str, str, str]]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT timestamp, type, message FROM system_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                (int(limit), max(int(offset), 0)),
            )
            return cur.fetchall()

    def clear_logs(self) -> int:
        """Удаляет все записи журнала и возвращает их количество.

        Вызывается только через защищённый API-эндпоинт с подтверждением
        пользователя. Сами тексты записей в отчёт не попадают.
        """
        with self._lock:
            with self.conn:
                cur = self.conn.execute("SELECT COUNT(*) FROM system_logs")
                row = cur.fetchone()
                removed = int(row[0]) if row else 0
            with self.conn:
                cur = self.conn.execute("DELETE FROM system_logs")
                deleted = int(cur.rowcount or 0)
        return deleted if deleted > 0 else removed

    def sqlite_datetime(self, modifier: str | None = None) -> str:
        """Возвращает локальное datetime для сравнения с created_at/timestamp."""
        if not modifier:
            return _local_now_str()
        normalized = str(modifier).strip().lower()
        if normalized in {"start of day", "start of month", "start of year"}:
            return _anchor_sqlite_modifier(normalized)
        return _local_datetime_str(_parse_sqlite_modifier(str(modifier)))

    # ---------- очередь постов ----------
    def save_post_to_queue(self, post: dict) -> int:
        """Кладёт пост в очередь ручной публикации. Возвращает id записи."""
        now = _local_now_str()
        with self._lock:
            with self.conn:
                cur = self.conn.execute(
                    """
                    INSERT INTO post_queue (
                        source_name, post_id, group_id, original_text, publish_text,
                        media_files, media_kind, category, target_chat, target_chats, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
                    ON CONFLICT(source_name, post_id) DO UPDATE SET
                        original_text = excluded.original_text,
                        publish_text = excluded.publish_text,
                        media_files = excluded.media_files,
                        media_kind = excluded.media_kind,
                        category = excluded.category,
                        target_chat = excluded.target_chat,
                        target_chats = excluded.target_chats,
                        updated_at = excluded.updated_at,
                        -- Ошибка снимается: содержимое изменилось, значит
                        -- пост снова можно публиковать. Отменённый и уже
                        -- опубликованный пост не трогаем: отмена — решение
                        -- пользователя, а повторная публикация не нужна.
                        status = CASE post_queue.status
                            WHEN 'failed' THEN 'draft'
                            ELSE post_queue.status
                        END
                    """,
                    (
                        str(post.get("source_name", "")),
                        str(post.get("post_id", "")),
                        str(post.get("group_id", "")),
                        str(post.get("original_text", "")),
                        str(post.get("publish_text", "")),
                        str(post.get("media_files", "[]")),
                        str(post.get("media_kind", "")),
                        str(post.get("category", "")),
                        str(post.get("target_chat", "")),
                        str(post.get("target_chats", "[]")),
                        now,
                        now,
                    ),
                )
                if cur.lastrowid:
                    return int(cur.lastrowid)
            cur = self.conn.execute(
                "SELECT id FROM post_queue WHERE source_name = ? AND post_id = ?",
                (str(post.get("source_name", "")), str(post.get("post_id", ""))),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def list_queued_posts(self, status: str = "", limit: int = 200) -> list[dict]:
        """Список постов очереди. Пустой status — все статусы."""
        sql = (
            "SELECT id, source_name, post_id, group_id, original_text, publish_text, "
            "media_files, media_kind, category, target_chat, target_chats, status, scheduled_at, "
            "publish_attempts, "
            "published_at, last_error, created_at, updated_at "
            "FROM post_queue"
        )
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
        columns = [
            "id", "source_name", "post_id", "group_id", "original_text", "publish_text",
            "media_files", "media_kind", "category", "target_chat", "target_chats", "status",
            "scheduled_at", "published_at", "last_error", "publish_attempts",
            "created_at", "updated_at",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def is_post_queued(self, source: str, post_id: int | str) -> bool:
        """Return whether a source post already has a pending queue entry.

        Only entries that still need work count: a draft waiting for the
        user, a scheduled post, or one the user cancelled. A failed entry
        does not block the source — such a post is worth another try.
        """
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT 1 FROM post_queue WHERE source_name = ? AND post_id = ? "
                "AND status IN ('draft', 'scheduled', 'cancelled') LIMIT 1",
                (str(source), str(post_id)),
            )
            return cur.fetchone() is not None

    def get_queued_post(self, post_id: int) -> dict | None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT id, source_name, post_id, group_id, original_text, publish_text, "
                "media_files, media_kind, category, target_chat, target_chats, status, scheduled_at, "
            "publish_attempts, "
                "published_at, last_error, created_at, updated_at "
                "FROM post_queue WHERE id = ?",
                (int(post_id),),
            )
            row = cur.fetchone()
        if not row:
            return None
        columns = [
            "id", "source_name", "post_id", "group_id", "original_text", "publish_text",
            "media_files", "media_kind", "category", "target_chat", "target_chats", "status",
            "scheduled_at", "published_at", "last_error", "publish_attempts",
            "created_at", "updated_at",
        ]
        return dict(zip(columns, row, strict=True))

    def get_queued_post_by_source(self, source: str, post_id: int | str) -> dict | None:
        """Черновик поста по источнику и идентификатору сообщения."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT id, source_name, post_id, group_id, original_text, publish_text, "
                "media_files, media_kind, category, target_chat, target_chats, status, scheduled_at, "
            "publish_attempts, "
                "published_at, last_error, created_at, updated_at "
                "FROM post_queue WHERE source_name = ? AND post_id = ?",
                (str(source), str(post_id)),
            )
            row = cur.fetchone()
        if not row:
            return None
        columns = [
            "id", "source_name", "post_id", "group_id", "original_text", "publish_text",
            "media_files", "media_kind", "category", "target_chat", "target_chats", "status",
            "scheduled_at", "published_at", "last_error", "publish_attempts",
            "created_at", "updated_at",
        ]
        return dict(zip(columns, row, strict=True))

    def update_queued_post(self, post_id: int, changes: dict) -> bool:
        allowed = {
            "publish_text", "category", "target_chat", "target_chats", "status",
            "scheduled_at", "media_files", "media_kind", "last_error", "published_at",
            "publish_attempts",
        }
        fields = {k: v for k, v in changes.items() if k in allowed}
        if not fields:
            return False
        assignments = ", ".join(f"{name} = ?" for name in fields)
        params = list(fields.values()) + [_local_now_str(), int(post_id)]
        with self._lock, self.conn:
            cur = self.conn.execute(
                f"UPDATE post_queue SET {assignments}, updated_at = ? WHERE id = ?",
                params,
            )
            return cur.rowcount > 0

    def delete_queued_post(self, post_id: int) -> bool:
        with self._lock:
            with self.conn:
                cur = self.conn.execute("DELETE FROM post_queue WHERE id = ?", (int(post_id),))
                return cur.rowcount > 0

    def due_scheduled_posts(self, limit: int = 20) -> list[dict]:
        """Отложенные посты, время публикации которых наступило."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT id, source_name, post_id, group_id, original_text, publish_text, "
                "media_files, media_kind, category, target_chat, target_chats, status, scheduled_at, "
            "publish_attempts, "
                "published_at, last_error, created_at, updated_at "
                "FROM post_queue WHERE status = 'scheduled' "
                "AND scheduled_at IS NOT NULL AND scheduled_at <= ? "
                "ORDER BY scheduled_at ASC LIMIT ?",
                (_local_now_str(), max(1, min(int(limit), 100))),
            )
            rows = cur.fetchall()
        columns = [
            "id", "source_name", "post_id", "group_id", "original_text", "publish_text",
            "media_files", "media_kind", "category", "target_chat", "target_chats", "status",
            "scheduled_at", "published_at", "last_error", "publish_attempts",
            "created_at", "updated_at",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def count_queued_posts(self) -> dict[str, int]:
        """Счётчики постов по статусам для панели."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT status, COUNT(*) FROM post_queue GROUP BY status")
            rows = cur.fetchall()
        return {str(status): int(count) for status, count in rows}

    def published_posts_with_media(self, older_than_days: int) -> list[dict]:
        """Опубликованные посты с медиа, у которых медиа старше срока хранения.

        Возвращает записи с полем `media_files` в виде списка имён файлов.
        """
        if int(older_than_days) <= 0:
            return []
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT id, media_files, published_at, updated_at, created_at "
                "FROM post_queue WHERE status = 'published' "
                "AND media_files IS NOT NULL AND media_files NOT IN ('', '[]')"
            )
            rows = cur.fetchall()
        cutoff = (datetime.now() - timedelta(days=int(older_than_days))).strftime("%Y-%m-%d %H:%M:%S")
        result: list[dict] = []
        for post_id, media_files, published_at, updated_at, created_at in rows:
            stamp = published_at or updated_at or created_at or ""
            if not stamp or str(stamp) > cutoff:
                continue
            try:
                names = json.loads(media_files or "[]")
            except (TypeError, ValueError):
                names = []
            if not isinstance(names, list) or not names:
                continue
            result.append({"id": post_id, "media_files": [str(n) for n in names]})
        return result

    def clear_post_media(self, post_id: int) -> list[str]:
        """Отвязывает медиа от поста и возвращает имена файлов для удаления."""
        row = self.get_queued_post(post_id)
        if not row:
            return []
        try:
            names = json.loads(row.get("media_files") or "[]")
        except (TypeError, ValueError):
            names = []
        if not isinstance(names, list):
            names = []
        names = [str(n) for n in names]
        self.update_queued_post(post_id, {"media_files": "[]", "media_kind": ""})
        return names

    def total_media_size(self) -> tuple[int, list[tuple[Any, Any]]]:
        """Суммарный размер и количество медиафайлов очереди (для оценки места)."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT id, media_files FROM post_queue "
                "WHERE media_files IS NOT NULL AND media_files NOT IN ('', '[]')"
            )
            rows = cur.fetchall()
        return len(rows), rows

    # ---------- агрегаты для модульных дашбордов ----------
    def count_since(self, table: str, since_iso: str) -> int:
        """Параметризованный SELECT COUNT(*) WHERE created_at >= ?.

        `table` — это `published_posts` или `skipped_posts` (любая с
        колонкой `created_at`). SQL-инъекция исключена: имя таблицы
        сверяется с whitelist'ом, аргумент `since_iso` — bind-parameter.
        """
        allowed = ("published_posts", "skipped_posts")
        if table not in allowed:
            raise ValueError(f"count_since: table {table!r} not in {allowed}")
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE created_at >= ?",
                (since_iso,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def count_total(self, table: str) -> int:
        """SELECT COUNT(*) FROM table — для total-счётчиков дашборда."""
        allowed = ("published_posts", "skipped_posts")
        if table not in allowed:
            raise ValueError(f"count_total: table {table!r} not in {allowed}")
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def count_logs_by_type(self, types: tuple[str, ...], since_iso: str | None = None) -> int:
        """Сколько логов с указанным type (LIKE-префиксом) за период.

        `types` — кортеж префиксов, например `("ERR-", "WARN-")`.
        `since_iso=None` означает «за всё время».
        """
        if not types:
            return 0
        with self._lock:
            cur = self.conn.cursor()
            if since_iso is None:
                cur.execute(
                    "SELECT COUNT(*) FROM system_logs WHERE "
                    + " OR ".join(["type LIKE ?"] * len(types)),
                    tuple(f"{t}%" for t in types),
                )
            else:
                cur.execute(
                    "SELECT COUNT(*) FROM system_logs WHERE "
                    + " AND ".join(
                        ["(" + " OR ".join(["type LIKE ?"] * len(types)) + ")"]
                    )
                    + " AND timestamp >= ?",
                    tuple([f"{t}%" for t in types] + [since_iso]),
                )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def top_published_sources(self, since_iso: str, limit: int = 5) -> list[tuple[str, int]]:
        """Топ источников по числу публикаций за период.

        Возвращает список (source_name, count) ORDER BY count DESC.
        """
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT source_name, COUNT(*) AS c FROM published_posts "
                "WHERE created_at >= ? AND source_name IS NOT NULL AND source_name <> '' "
                "GROUP BY source_name ORDER BY c DESC LIMIT ?",
                (since_iso, int(limit)),
            )
            return [(row[0], int(row[1])) for row in cur.fetchall()]

    def count_active_published_sources_since(self, since_iso: str) -> int:
        """Counts distinct non-empty sources with a published post since a timestamp."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT COUNT(DISTINCT source_name) FROM published_posts "
                "WHERE created_at >= ? AND source_name IS NOT NULL AND source_name <> ''",
                (since_iso,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def top_skipped_reasons(self, since_iso: str) -> dict[str, int]:
        """Распределение причин пропуска за период: {reason: count}."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT reason, COUNT(*) AS c FROM skipped_posts "
                "WHERE created_at >= ? AND reason IS NOT NULL "
                "GROUP BY reason ORDER BY c DESC",
                (since_iso,),
            )
            return {row[0]: int(row[1]) for row in cur.fetchall()}

    def last_published(self) -> tuple[str | None, str | None]:
        """Последний успешный пост: (created_at, source_name).

        None-значения если таблица пуста или колонки created_at ещё нет
        (для совместимости с очень старыми БД).
        """
        with self._lock:
            cur = self.conn.cursor()
            try:
                cur.execute(
                    "SELECT created_at, source_name FROM published_posts "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            except sqlite3.OperationalError:
                return (None, None)
            row = cur.fetchone()
            if not row:
                return (None, None)
            return (row[0], row[1])

    def last_log_by_prefix(self, type_prefix: str) -> tuple[str | None, str | None, str | None] | None:
        """Последняя запись лога с указанным префиксом type.

        Возвращает (timestamp, type, message) или None.
        """
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT timestamp, type, message FROM system_logs "
                "WHERE type LIKE ? ORDER BY id DESC LIMIT 1",
                (f"{type_prefix}%",),
            )
            row = cur.fetchone()
            if not row:
                return None
            return (row[0], row[1], row[2])

    # ---------- удобные обёртки для дашборда ----------
    def count_published_since(self, since_iso: str) -> int:
        return self.count_since("published_posts", since_iso)

    def count_skipped_since(self, since_iso: str) -> int:
        return self.count_since("skipped_posts", since_iso)

    # ---------- behavior module ----------
    def add_behavior_session(
        self,
        *,
        target_url: str = "",
        keyword: str = "",
        scenario: str = "",
        pages_visited: int = 0,
        clicks: int = 0,
        dwell_sec: int = 0,
        success: bool = False,
        error_code: str = "",
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> int:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO behavior_sessions (
                    started_at, finished_at, target_url, keyword, scenario,
                    pages_visited, clicks, dwell_sec, success, error_code
                ) VALUES (
                    COALESCE(?, CURRENT_TIMESTAMP), ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    started_at,
                    finished_at,
                    target_url,
                    keyword,
                    scenario,
                    max(int(pages_visited), 0),
                    max(int(clicks), 0),
                    max(int(dwell_sec), 0),
                    1 if success else 0,
                    error_code,
                ),
            )
            if cur.lastrowid is not None:
                return int(cur.lastrowid)
            return 0

    def get_behavior_stats_snapshot(self) -> dict[str, Any]:
        with self._lock:
            row = self.conn.execute(
                """
                SELECT
                    COUNT(*) AS sessions_total,
                    SUM(CASE WHEN date(started_at) = date('now', 'localtime') THEN 1 ELSE 0 END) AS sessions_today,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_total,
                    AVG(CASE WHEN dwell_sec > 0 THEN dwell_sec END) AS avg_dwell_sec,
                    MAX(COALESCE(finished_at, started_at)) AS last_session_at,
                    COUNT(DISTINCT CASE WHEN target_url IS NOT NULL AND target_url <> '' THEN target_url END) AS targets_count
                FROM behavior_sessions
                """
            ).fetchone()

            sessions_total = int(row[0] or 0) if row else 0
            sessions_today = int(row[1] or 0) if row else 0
            success_total = int(row[2] or 0) if row else 0
            avg_dwell_sec = int(float(row[3])) if row and row[3] is not None else 0
            last_session_at = row[4] if row else None
            dynamic_targets = int(row[5] or 0) if row else 0

            configured_targets = 0
            raw_row = self.conn.execute(
                "SELECT value FROM bot_settings WHERE key='behavior_target_urls'"
            ).fetchone()
            raw_targets = raw_row[0] if raw_row and raw_row[0] else ""
            if raw_targets.strip():
                configured_targets = len([line for line in raw_targets.splitlines() if line.strip()])

            targets_count = max(dynamic_targets, configured_targets)
            success_rate = round((success_total * 100.0 / sessions_total), 2) if sessions_total else 0.0

            return {
                "sessions_today": sessions_today,
                "sessions_total": sessions_total,
                "success_rate": success_rate,
                "avg_dwell_sec": avg_dwell_sec,
                "targets_count": targets_count,
                "last_session_at": last_session_at,
            }

    # ---------- lifecycle ----------
    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()


# === Singleton ===
# Все модули (main, Site) импортируют этот объект, а не создают DB() сами.
# Это устраняет двойную инициализацию PRAGMA и конкуренцию за лок-файл.
_shared_db: DB | None = None


def get_db() -> DB:
    global _shared_db
    if _shared_db is None:
        _shared_db = DB(DB_PATH)
    return _shared_db
