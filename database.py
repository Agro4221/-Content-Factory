# REFACTORED: navigation / UX / UI restructured per expert spec v2.0
"""
SQLite-обёртка для проекта Project MaryJane.

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
  - **Дефолты разделены на активные (этот файл) и зарезервированные для
    будущих модулей** (future_defaults.py). Бот использует только активные.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Callable


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


def _anchor_sqlite_modifier(modifier: str) -> str | None:
    """Разбирает якорные модификаторы SQLite ('start of day/month/year').

    Возвращает абсолютную локальную метку времени в формате SQLite DATETIME
    или None, если модификатор не является якорным (тогда используется
    относительный разбор через `_parse_sqlite_modifier`).
    """
    raw = modifier.strip().lower()
    now = datetime.now()
    if raw == "start of day":
        anchored = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif raw == "start of month":
        anchored = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif raw == "start of year":
        anchored = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None
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

from config import DB_PATH, make_post_key
from future_defaults import ALL_FUTURE_DEFAULTS

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
MIGRATIONS: list[tuple[int, Callable[["DB"], None]]] = []


def _migration_v1(db: "DB") -> None:
    """v1: начальная схема — все 4 таблицы, дефолтные настройки, составной ключ."""
    db._create_initial_tables()
    db._init_default_settings()


def _migration_v2(db: "DB") -> None:
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
        "CREATE INDEX IF NOT EXISTS idx_published_created_at "
        "ON published_posts(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_published_source_created_at "
        "ON published_posts(source_name, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_skipped_created_at "
        "ON skipped_posts(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_skipped_reason "
        "ON skipped_posts(reason)",
        "CREATE INDEX IF NOT EXISTS idx_logs_type_timestamp "
        "ON system_logs(type, timestamp)",
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


def _migration_v3(db: "DB") -> None:
    """v3: storage for behavior/reviews modules (Milestone A)."""
    statements = (
        """
        CREATE TABLE IF NOT EXISTS behavior_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            target_url TEXT,
            keyword TEXT,
            scenario TEXT,
            pages_visited INTEGER DEFAULT 0,
            clicks INTEGER DEFAULT 0,
            dwell_sec INTEGER DEFAULT 0,
            success INTEGER DEFAULT 0,
            error_code TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS reviews_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            credentials_enc TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            warmup_until TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT,
            notes TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS reviews_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            target TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            error_code TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            published_at TEXT,
            FOREIGN KEY(account_id) REFERENCES reviews_accounts(id) ON DELETE SET NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS reviews_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_behavior_sessions_started_at ON behavior_sessions(started_at)",
        "CREATE INDEX IF NOT EXISTS idx_behavior_sessions_success ON behavior_sessions(success)",
        "CREATE INDEX IF NOT EXISTS idx_reviews_accounts_status ON reviews_accounts(status)",
        "CREATE INDEX IF NOT EXISTS idx_reviews_queue_status_created_at ON reviews_queue(status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_reviews_queue_published_at ON reviews_queue(published_at)",
    )
    with db.conn:
        for stmt in statements:
            db.conn.execute(stmt)


def _migration_v4(db: "DB") -> None:
    """v4: resilient reviews queue schema (dedupe + retries)."""

    columns = {
        row[1]
        for row in db.conn.execute("PRAGMA table_info(reviews_queue)").fetchall()
    }

    with db.conn:
        if "source" not in columns:
            db.conn.execute(
                "ALTER TABLE reviews_queue ADD COLUMN source TEXT NOT NULL DEFAULT 'internal'"
            )
        if "external_id" not in columns:
            db.conn.execute(
                "ALTER TABLE reviews_queue ADD COLUMN external_id TEXT NOT NULL DEFAULT ''"
            )
        if "normalized_text" not in columns:
            db.conn.execute(
                "ALTER TABLE reviews_queue ADD COLUMN normalized_text TEXT NOT NULL DEFAULT ''"
            )
        if "fingerprint" not in columns:
            db.conn.execute(
                "ALTER TABLE reviews_queue ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''"
            )
        if "attempts" not in columns:
            db.conn.execute(
                "ALTER TABLE reviews_queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
            )
        if "next_retry_at" not in columns:
            db.conn.execute(
                "ALTER TABLE reviews_queue ADD COLUMN next_retry_at TEXT"
            )

        db.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_reviews_queue_source_external "
            "ON reviews_queue(source, external_id) "
            "WHERE external_id <> ''"
        )
        db.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_reviews_queue_fingerprint "
            "ON reviews_queue(fingerprint) "
            "WHERE fingerprint <> ''"
        )
        db.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reviews_queue_next_retry_at "
            "ON reviews_queue(next_retry_at)"
        )
        db.conn.execute(
            "UPDATE reviews_queue SET source = 'internal' "
            "WHERE source IS NULL OR source = ''"
        )
        db.conn.execute(
            "UPDATE reviews_queue SET external_id = '' "
            "WHERE external_id IS NULL"
        )
        db.conn.execute(
            "UPDATE reviews_queue SET normalized_text = '' "
            "WHERE normalized_text IS NULL"
        )
        db.conn.execute(
            "UPDATE reviews_queue SET fingerprint = '' "
            "WHERE fingerprint IS NULL"
        )


def _migration_v5(db: "DB") -> None:
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


# Регистрируем миграции
MIGRATIONS.append((1, _migration_v1))
MIGRATIONS.append((2, _migration_v2))
MIGRATIONS.append((3, _migration_v3))
MIGRATIONS.append((4, _migration_v4))
MIGRATIONS.append((5, _migration_v5))

CURRENT_SCHEMA_VERSION: int = max(v for v, _ in MIGRATIONS)


# Дефолты вынесены в константу — DRY и легче расширять.
# Только то, что БОТ использует прямо сейчас. Зарезервированные дефолты
# для будущих модулей (Поведенческий фактор / Отзывы) лежат в
# future_defaults.py — они подтягиваются в БД через SETTING_KEYS
# whitelist, но не нужны боту для работы.
_DEFAULT_SETTINGS: dict[str, str] = {
    # --- Telegram (глобально) ---
    "tg_api_id": "",
    "tg_api_hash": "",
    "bot_token": "",

    # --- AI ---
    "ai_mode": "LOCAL",
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

    # --- Лицензия ---
    "license_key": "",
}

# Сливаем с зарезервированными дефолтами из future_defaults.py.
# Эти ключи попадут в БД при первом запуске (для админки и будущих модулей),
# но main.py их сейчас не читает.
_DEFAULT_SETTINGS = {**_DEFAULT_SETTINGS, **ALL_FUTURE_DEFAULTS}


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
        self._run_migrations()
        self._migrate_legacy_post_keys()  # для очень старых БД до версионирования

    # ---------- init ----------
    def _configure_pragmas(self) -> None:
        with self.conn:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.execute("PRAGMA foreign_keys=ON")

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
            except sqlite3.Error as e:
                # Не получилось — БД остаётся на старой версии, при следующем
                # запуске миграция повторится.
                _logger.error("[DB] Migration v%d failed: %s", version, e)
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
            # post_key — составной (source:id), post_id — оригинал для отладки
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
        except sqlite3.Error:
            return
        if "post_key" in cols:
            return  # уже мигрировано
        try:
            with self.conn:
                # Копируем старые данные с переименованием в новый формат
                self.conn.execute("ALTER TABLE published_posts RENAME TO _published_posts_legacy")
                self.conn.execute(
                    """
                    CREATE TABLE published_posts (
                        post_key TEXT PRIMARY KEY,
                        post_id TEXT,
                        source_name TEXT
                    )
                    """
                )
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO published_posts (post_key, post_id, source_name)
                    SELECT (source_name || ':' || post_id), post_id, source_name
                    FROM _published_posts_legacy
                    """
                )
                self.conn.execute("DROP TABLE _published_posts_legacy")
                _logger.info("Migrated published_posts to composite key")
        except sqlite3.Error as e:
            _logger.warning("Migration of published_posts failed (non-fatal): %s", e)

        # Та же история для skipped_posts — на случай, если в БД она была создана
        # ещё до моего фикса (там post_id как PK).
        try:
            cols = [row[1] for row in self.conn.execute("PRAGMA table_info(skipped_posts)").fetchall()]
        except sqlite3.Error:
            cols = []
        if cols and "post_key" not in cols:
            try:
                with self.conn:
                    self.conn.execute("ALTER TABLE skipped_posts RENAME TO _skipped_posts_legacy")
                    self.conn.execute(
                        """
                        CREATE TABLE skipped_posts (
                            post_key TEXT PRIMARY KEY,
                            post_id TEXT,
                            source_name TEXT,
                            reason TEXT
                        )
                        """
                    )
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO skipped_posts (post_key, post_id, source_name, reason)
                        SELECT (source_name || ':' || post_id), post_id, source_name, reason
                        FROM _skipped_posts_legacy
                        """
                    )
                    self.conn.execute("DROP TABLE _skipped_posts_legacy")
                    _logger.info("Migrated skipped_posts to composite key")
            except sqlite3.Error as e:
                _logger.warning("Migration of skipped_posts failed (non-fatal): %s", e)

    def _init_default_settings(self) -> None:
        with self.conn:
            for k, v in _DEFAULT_SETTINGS.items():
                self.conn.execute(
                    "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
                    (k, v),
                )

    # ---------- settings ----------
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT value FROM bot_settings WHERE key=?", (key,))
            row = cur.fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            with self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
                    (key, str(value)),
                )

    def get_all_settings(self) -> dict[str, str]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT key, value FROM bot_settings")
            return {row[0]: row[1] for row in cur.fetchall()}

    # ---------- credentials (reviews accounts) ----------
    def _credentials_seed(self) -> bytes:
        """Per-install seed for reversible local obfuscation.

        Milestone A: avoid plaintext storage without introducing new runtime deps.
        """
        try:
            row = self.conn.execute(
                "SELECT value FROM bot_settings WHERE key='license_key'"
            ).fetchone()
            license_key = row[0] if row and row[0] else ""
        except sqlite3.Error:
            license_key = ""
        hwid = "unknown-hwid"
        try:
            # Late import: Auth imports database.get_db at module import time.
            import Auth  # type: ignore

            hwid = Auth.get_hwid()
        except Exception:
            pass
        return hashlib.sha256(
            f"{license_key}|{hwid}|project-maryjane-cred-v1".encode("utf-8")
        ).digest()

    @staticmethod
    def _stream_xor(seed: bytes, payload: bytes) -> bytes:
        out = bytearray(len(payload))
        counter = 0
        idx = 0
        while idx < len(payload):
            block = hmac.new(seed, counter.to_bytes(4, "big"), hashlib.sha256).digest()
            chunk = min(len(block), len(payload) - idx)
            for i in range(chunk):
                out[idx + i] = payload[idx + i] ^ block[i]
            idx += chunk
            counter += 1
        return bytes(out)

    def encrypt_credentials(self, raw: str) -> str:
        if not raw:
            return ""
        payload = raw.encode("utf-8")
        salt = os.urandom(8)
        token = self._stream_xor(self._credentials_seed() + salt, payload)
        return base64.urlsafe_b64encode(salt + token).decode("ascii")

    def decrypt_credentials(self, encrypted: str) -> str:
        if not encrypted:
            return ""
        try:
            blob = base64.urlsafe_b64decode(encrypted.encode("ascii"))
            if len(blob) < 8:
                return ""
            salt, cipher = blob[:8], blob[8:]
            payload = self._stream_xor(self._credentials_seed() + salt, cipher)
            return payload.decode("utf-8", errors="ignore")
        except Exception:
            return ""

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
        """Помечает пост как успешно опубликованный."""
        with self._lock:
            try:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO published_posts (post_key, post_id, source_name) VALUES (?, ?, ?)",
                        (make_post_key(source, post_id), str(post_id), source),
                    )
            except sqlite3.IntegrityError:
                pass  # дубль — норма
            except sqlite3.Error as e:
                _logger.error("save_post failed for %s/%s: %s", source, post_id, e)

    def save_skipped_post(self, source: str, post_id: int | str, reason: str) -> None:
        """Помечает пост как пропущенный (реклама/пустой)."""
        with self._lock:
            try:
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO skipped_posts (post_key, post_id, source_name, reason) VALUES (?, ?, ?, ?)",
                        (make_post_key(source, post_id), str(post_id), source, reason),
                    )
            except sqlite3.IntegrityError:
                pass
            except sqlite3.Error as e:
                _logger.error("save_skipped_post failed for %s/%s: %s", source, post_id, e)

    # ---------- logs ----------
    def add_log(self, log_type: str, message: str) -> None:
        with self._lock:
            with self.conn:
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

    def sqlite_datetime(self, modifier: str | None = None) -> str:
        """Возвращает локальное datetime для сравнения с created_at/timestamp."""
        if not modifier:
            return _local_now_str()
        anchored = _anchor_sqlite_modifier(str(modifier))
        if anchored is not None:
            return anchored
        return _local_datetime_str(_parse_sqlite_modifier(str(modifier)))

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
                    f"SELECT COUNT(*) FROM system_logs WHERE "
                    + " OR ".join(["type LIKE ?"] * len(types)),
                    tuple(f"{t}%" for t in types),
                )
            else:
                cur.execute(
                    f"SELECT COUNT(*) FROM system_logs WHERE "
                    + " AND ".join(
                        ["(" + " OR ".join(["type LIKE ?"] * len(types)) + ")"]
                    )
                    + " AND timestamp >= ?",
                    tuple(list(f"{t}%" for t in types) + [since_iso]),
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
            return int(cur.lastrowid)

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

    # ---------- reviews module ----------
    def create_reviews_account(
        self,
        *,
        email: str,
        password: str,
        recovery: str = "",
        status: str = "new",
        warmup_until: str | None = None,
        notes: str = "",
    ) -> int | None:
        email_norm = (email or "").strip().lower()
        if not email_norm:
            return None
        credentials = self.encrypt_credentials(f"{password}:{recovery}")
        if not credentials:
            return None

        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO reviews_accounts (
                    email, credentials_enc, status, warmup_until, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (email_norm, credentials, status, warmup_until, notes),
            )
            if cur.rowcount == 0:
                return None
            return int(cur.lastrowid)

    def list_reviews_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT
                    id, email, status, warmup_until,
                    created_at, updated_at, last_used_at, notes
                FROM reviews_accounts
                ORDER BY id DESC
                """
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                result.append(
                    {
                        "id": int(row[0]),
                        "email": row[1],
                        "status": row[2],
                        "warmup_until": row[3],
                        "created_at": row[4],
                        "updated_at": row[5],
                        "last_used_at": row[6],
                        "notes": row[7] or "",
                    }
                )
            return result

    def delete_reviews_account(self, account_id: int) -> bool:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM reviews_accounts WHERE id = ?", (int(account_id),))
            return cur.rowcount > 0

    def create_reviews_accounts_batch(self, count: int) -> int:
        generated = 0
        safe_count = max(0, min(int(count), 100))
        for _ in range(safe_count):
            salt = base64.urlsafe_b64encode(os.urandom(6)).decode("ascii").rstrip("=")
            email = f"generated_{salt}@example.invalid"
            password = base64.urlsafe_b64encode(os.urandom(9)).decode("ascii").rstrip("=")
            recovery = f"recovery_{salt}@example.invalid"
            if self.create_reviews_account(email=email, password=password, recovery=recovery):
                generated += 1
        return generated

    def import_reviews_accounts(self, lines: list[str]) -> dict[str, int]:
        created = 0
        skipped = 0
        for line in lines:
            raw = (line or "").strip()
            if not raw:
                continue
            parts = [part.strip() for part in raw.split(":", 2)]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                skipped += 1
                continue
            email, password = parts[0], parts[1]
            recovery = parts[2] if len(parts) > 2 else ""
            account_id = self.create_reviews_account(
                email=email,
                password=password,
                recovery=recovery,
                status="imported",
            )
            if account_id:
                created += 1
            else:
                skipped += 1
        return {"created": created, "skipped": skipped}

    def enqueue_review_item(
        self,
        *,
        target: str,
        payload: str = "{}",
        account_id: int | None = None,
        source: str = "internal",
        external_id: str = "",
        normalized_text: str = "",
        fingerprint: str = "",
        status: str = "pending",
    ) -> int | None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO reviews_queue (
                    account_id,
                    target,
                    payload,
                    status,
                    source,
                    external_id,
                    normalized_text,
                    fingerprint,
                    attempts,
                    next_retry_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, CURRENT_TIMESTAMP)
                """,
                (
                    account_id,
                    target,
                    payload or "{}",
                    status,
                    (source or "internal").strip(),
                    (external_id or "").strip(),
                    normalized_text or "",
                    (fingerprint or "").strip(),
                ),
            )
            if cur.rowcount > 0:
                return int(cur.lastrowid)

            row = None
            if external_id:
                row = self.conn.execute(
                    """
                    SELECT id
                    FROM reviews_queue
                    WHERE source = ? AND external_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    ((source or "internal").strip(), (external_id or "").strip()),
                ).fetchone()
            elif fingerprint:
                row = self.conn.execute(
                    """
                    SELECT id
                    FROM reviews_queue
                    WHERE fingerprint = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    ((fingerprint or "").strip(),),
                ).fetchone()
            return int(row[0]) if row else None

    def fetch_pending_review_items(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT
                    id,
                    account_id,
                    target,
                    payload,
                    source,
                    external_id,
                    normalized_text,
                    attempts,
                    created_at
                FROM reviews_queue
                WHERE status = 'pending'
                  AND (next_retry_at IS NULL OR next_retry_at <= CURRENT_TIMESTAMP)
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                result.append(
                    {
                        "id": int(row[0]),
                        "account_id": row[1],
                        "target": row[2] or "",
                        "payload": row[3] or "{}",
                        "source": row[4] or "internal",
                        "external_id": row[5] or "",
                        "normalized_text": row[6] or "",
                        "attempts": int(row[7] or 0),
                        "created_at": row[8],
                    }
                )
            return result

    def review_item_exists(
        self,
        *,
        source: str,
        external_id: str,
        fingerprint: str = "",
    ) -> bool:
        src = (source or "internal").strip()
        ext = (external_id or "").strip()
        fp = (fingerprint or "").strip()
        with self._lock:
            row = self.conn.execute(
                """
                SELECT 1
                FROM reviews_queue
                WHERE source = ? AND external_id = ?
                LIMIT 1
                """,
                (src, ext),
            ).fetchone()
            if row:
                return True
            if fp:
                row = self.conn.execute(
                    """
                    SELECT 1
                    FROM reviews_queue
                    WHERE fingerprint = ?
                    LIMIT 1
                    """,
                    (fp,),
                ).fetchone()
                return bool(row)
            return False

    def mark_review_item_status(
        self,
        item_id: int,
        *,
        status: str,
        error_code: str = "",
        attempts: int | None = None,
        retry_in_seconds: int | None = None,
    ) -> bool:
        with self._lock:
            sets = ["status = ?", "error_code = ?", "updated_at = CURRENT_TIMESTAMP"]
            params: list[Any] = [status, error_code or ""]
            if status == "published":
                sets.append("published_at = CURRENT_TIMESTAMP")
            if attempts is not None:
                sets.append("attempts = ?")
                params.append(max(0, int(attempts)))
            if retry_in_seconds is None:
                sets.append("next_retry_at = NULL")
            else:
                sets.append("next_retry_at = ?")
                params.append(_local_datetime_str(int(retry_in_seconds)))
            params.append(int(item_id))
            cur = self.conn.execute(
                f"UPDATE reviews_queue SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
            return cur.rowcount > 0

    def get_reviews_status_counts(self) -> dict[str, int]:
        with self._lock:
            accounts_row = self.conn.execute(
                "SELECT COUNT(*) FROM reviews_accounts"
            ).fetchone()
            queue_row = self.conn.execute(
                "SELECT COUNT(*) FROM reviews_queue WHERE status = 'pending'"
            ).fetchone()
            return {
                "accounts_total": int(accounts_row[0] or 0) if accounts_row else 0,
                "queue_pending": int(queue_row[0] or 0) if queue_row else 0,
            }

    def get_reviews_stats_snapshot(self) -> dict[str, Any]:
        with self._lock:
            accounts_row = self.conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status IN ('new', 'imported', 'active', 'ready') THEN 1 ELSE 0 END) AS active
                FROM reviews_accounts
                """
            ).fetchone()
            queue_row = self.conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) AS published_total,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_total,
                    SUM(CASE WHEN status = 'published' AND date(published_at) = date('now', 'localtime') THEN 1 ELSE 0 END) AS published_today,
                    MAX(published_at) AS last_publish_at
                FROM reviews_queue
                """
            ).fetchone()

            return {
                "accounts_total": int(accounts_row[0] or 0) if accounts_row else 0,
                "accounts_active": int(accounts_row[1] or 0) if accounts_row else 0,
                "queue_pending": int(queue_row[0] or 0) if queue_row else 0,
                "published_total": int(queue_row[1] or 0) if queue_row else 0,
                "failed_total": int(queue_row[2] or 0) if queue_row else 0,
                "published_today": int(queue_row[3] or 0) if queue_row else 0,
                "last_publish_at": queue_row[4] if queue_row else None,
            }

    def pick_reviews_account(self) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id, email, status, warmup_until, created_at, updated_at, last_used_at, notes
                FROM reviews_accounts
                WHERE status IN ('ready', 'active', 'imported', 'new', 'warming')
                  AND (
                    warmup_until IS NULL
                    OR warmup_until = ''
                    OR datetime(warmup_until) <= datetime('now', 'localtime')
                  )
                ORDER BY (last_used_at IS NULL) DESC, last_used_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            return {
                "id": int(row[0]),
                "email": row[1] or "",
                "status": row[2] or "",
                "warmup_until": row[3],
                "created_at": row[4],
                "updated_at": row[5],
                "last_used_at": row[6],
                "notes": row[7] or "",
            }

    def get_reviews_account(self, account_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                """
                SELECT id, email, status, warmup_until, created_at, updated_at, last_used_at, notes
                FROM reviews_accounts
                WHERE id = ?
                """,
                (int(account_id),),
            ).fetchone()
            if not row:
                return None
            return {
                "id": int(row[0]),
                "email": row[1] or "",
                "status": row[2] or "",
                "warmup_until": row[3],
                "created_at": row[4],
                "updated_at": row[5],
                "last_used_at": row[6],
                "notes": row[7] or "",
            }

    def touch_reviews_account(self, account_id: int) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE reviews_accounts
                SET last_used_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(account_id),),
            )

    def advance_reviews_account_warmup(self, account_id: int) -> None:
        settings = self.get_all_settings()
        try:
            warmup_days = max(
                0,
                int(str(settings.get("reviews_warmup_days", "7") or "7").strip()),
            )
        except ValueError:
            warmup_days = 7

        with self._lock:
            row = self.conn.execute(
                "SELECT status, warmup_until FROM reviews_accounts WHERE id = ?",
                (int(account_id),),
            ).fetchone()
            if not row:
                return
            status = (row[0] or "").strip().lower()
            warmup_until = row[1]
            if status in {"banned", "disabled", "ready", "active"}:
                return
            if warmup_days <= 0:
                self.conn.execute(
                    """
                    UPDATE reviews_accounts
                    SET status = 'ready', warmup_until = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (int(account_id),),
                )
                return
            if not warmup_until:
                self.conn.execute(
                    """
                    UPDATE reviews_accounts
                    SET status = 'warming',
                        warmup_until = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (_local_datetime_str(warmup_days * 86400), int(account_id)),
                )
                return
            warmed = self.conn.execute(
                """
                SELECT 1
                WHERE datetime(?) <= datetime('now', 'localtime')
                """,
                (warmup_until,),
            ).fetchone()
            if warmed:
                self.conn.execute(
                    """
                    UPDATE reviews_accounts
                    SET status = 'ready', warmup_until = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (int(account_id),),
                )

    def _collect_review_targets_from_settings(self, settings: dict[str, str]) -> list[tuple[str, str, str, str]]:
        entries: list[tuple[str, str, str, str]] = []
        seen: set[str] = set()

        def add(target: str, source: str, external_id: str, payload: dict[str, Any]) -> None:
            cleaned = (target or "").strip()
            if not cleaned or cleaned in seen:
                return
            seen.add(cleaned)
            entries.append(
                (
                    cleaned,
                    source,
                    external_id or cleaned,
                    json.dumps(payload, ensure_ascii=False),
                )
            )

        for line in (settings.get("reviews_target_cards") or "").splitlines():
            add(line, "target_card", line.strip(), {"kind": "target_card"})

        for part in re.split(r"[,;\n]", settings.get("reviews_place_ids") or ""):
            add(part, "place_id", part.strip(), {"kind": "place_id"})

        place_url = (settings.get("reviews_place_url") or "").strip()
        if place_url:
            add(place_url, "place_url", place_url, {"kind": "place_url"})

        return entries

    def sync_reviews_queue_from_targets(self) -> int:
        settings = self.get_all_settings()
        entries = self._collect_review_targets_from_settings(settings)
        added = 0
        for target, source, external_id, payload in entries:
            with self._lock:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO reviews_targets (target, active)
                    VALUES (?, 1)
                    """,
                    (target,),
                )
            item_id = self.enqueue_review_item(
                target=target,
                payload=payload,
                source=source,
                external_id=external_id,
                fingerprint=f"{source}:{external_id}",
            )
            if item_id:
                added += 1
        return added

    def list_reviews_queue_items(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT id, account_id, target, status, source, external_id,
                       attempts, error_code, created_at, published_at
                FROM reviews_queue
                ORDER BY id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            return [
                {
                    "id": int(row[0]),
                    "account_id": row[1],
                    "target": row[2] or "",
                    "status": row[3] or "",
                    "source": row[4] or "",
                    "external_id": row[5] or "",
                    "attempts": int(row[6] or 0),
                    "error_code": row[7] or "",
                    "created_at": row[8],
                    "published_at": row[9],
                }
                for row in rows
            ]

    # ---------- lifecycle ----------
    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


# === Singleton ===
# Все модули (main, Site, Auth) импортируют этот объект, а не создают DB() сами.
# Это устраняет двойную инициализацию PRAGMA и конкуренцию за лок-файл.
_shared_db: DB | None = None


def get_db() -> DB:
    global _shared_db
    if _shared_db is None:
        _shared_db = DB(DB_PATH)
    return _shared_db
