"""Конфигурация приложения Project MaryJane / Parser v1.28.3.6."""
from __future__ import annotations

import math
import os
import re
from urllib.parse import urlsplit

from env_loader import load_environment
from paths import BASE_DIR, BUNDLE_DIR, is_frozen


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read a bounded integer from the environment without crashing app import."""
    raw = os.environ.get(name, str(default)).strip()
    try:
        if not raw.isascii() or not raw.isdigit():
            raise ValueError("not an ASCII integer")
        value = int(raw)
    except ValueError:
        print(f"[WARN] Некорректный {name}; используется значение по умолчанию.")
        return default
    if not minimum <= value <= maximum:
        print(f"[WARN] {name} вне допустимого диапазона; используется значение по умолчанию.")
        return default
    return value


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """Read a finite bounded float from the environment without crashing app import."""
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        print(f"[WARN] Некорректный {name}; используется значение по умолчанию.")
        return default
    if not math.isfinite(value) or not minimum <= value <= maximum:
        print(f"[WARN] {name} вне допустимого диапазона; используется значение по умолчанию.")
        return default
    return value


load_environment()

APP_VERSION = "1.29.0.0"

API_HOST = os.environ.get("API_HOST", "127.0.0.1").strip() or "127.0.0.1"
API_PORT = _env_int("API_PORT", 8000, minimum=1, maximum=65535)
API_LOG_LEVEL = os.environ.get("API_LOG_LEVEL", "warning").strip().lower()
if API_LOG_LEVEL not in {"critical", "error", "warning", "info", "debug", "trace"}:
    print("[WARN] Некорректный API_LOG_LEVEL; используется значение по умолчанию.")
    API_LOG_LEVEL = "warning"
BROWSER_OPEN_ON_START = os.environ.get("BROWSER_OPEN_ON_START", "1").strip().lower() in {"1", "true", "yes", "on"}
BROWSER_OPEN_DELAY_S = _env_float("BROWSER_OPEN_DELAY_S", 2.5, minimum=0.0, maximum=60.0)

DB_PATH = os.path.join(BASE_DIR, "bot_storage.db")
SESSION_PATH = os.path.join(BASE_DIR, "session_name")
TEMP_DIR = os.path.join(BASE_DIR, "temp_media")
QUEUE_MEDIA_DIR = os.path.join(BASE_DIR, "queue_media")
ADMIN_HTML_PATH = os.path.join(BUNDLE_DIR, "admin.html")

MODULE_POSTS = "posts"
ALL_MODULES = frozenset({MODULE_POSTS})

POLL_PAUSE_S = 1
SLEEP_BETWEEN_POSTS_S = 5
SLEEP_API_ERROR_S = 30
SLEEP_FLOOD_POLL_S = 1

AI_REQUEST_TIMEOUT_S = 90
AI_TEMPERATURE = 0.4
AI_TRUNCATE_FALLBACK = 300

MIN_AD_WORD_LEN = 3
POLL_DELAY_DEFAULT = 60

ENV_DEFAULT_SETTINGS: dict[str, str] = {
    k: v for k, v in {
        "tg_api_id": os.environ.get("TG_API_ID", "").strip(),
        "tg_api_hash": os.environ.get("TG_API_HASH", "").strip(),
        "bot_token": os.environ.get("BOT_TOKEN", "").strip(),
        "remote_url": os.environ.get("REMOTE_URL", "").strip(),
        "remote_api_key": os.environ.get("REMOTE_API_KEY", "").strip(),
        "proxy_host": os.environ.get("PROXY_HOST", "").strip(),
        "proxy_port": os.environ.get("PROXY_PORT", "").strip(),
        "proxy_user": os.environ.get("PROXY_USER", "").strip(),
        "proxy_pass": os.environ.get("PROXY_PASS", "").strip(),
    }.items() if v
}

SETTING_KEYS = frozenset({
    "tg_api_id", "tg_api_hash", "bot_token", "target_channel",
    "ai_mode", "local_ai_provider", "local_host", "local_port", "local_model",
    "remote_url", "remote_api_key", "remote_model", "system_prompt",
    "sources", "topic_channels", "ads_words", "watermark_text",
    "poll_delay", "use_proxy", "proxy_type", "proxy_host", "proxy_port",
    "proxy_user", "proxy_pass", "mtproto_key",
    "publish_mode", "media_retention_days", "media_cleanup_enabled",
})

# Настройки, которые хранятся в базе в зашифрованном виде (Windows DPAPI).
# Их значения никогда не выводятся в журнал, терминал или панель управления.
SECRET_SETTING_KEYS = frozenset({
    "bot_token",
    "tg_api_hash",
    "remote_api_key",
    "proxy_pass",
    "mtproto_key",
})

#: Плейсхолдер, который панель показывает вместо сохранённого секрета.
SECRET_MASK = "••••••••"

#: Значение-сигнал, которым панель просит удалить сохранённый секрет.
SECRET_CLEAR_SIGNAL = "__clear__"

#: Режим публикации: auto — сразу после сбора, manual — через очередь постов.
PUBLISH_MODES = ("auto", "manual")
DEFAULT_PUBLISH_MODE = "auto"

#: Сколько дней хранить медиафайлы опубликованных постов. 0 — хранить вечно.
MEDIA_RETENTION_DAYS = (0, 1, 2, 3, 7)
DEFAULT_MEDIA_RETENTION_DAYS = 3

#: Как часто (в часах) парсер проверяет очередь на просроченные медиа.
MEDIA_CLEANUP_INTERVAL_HOURS = 6

#: Локальные провайдеры AI: ключ -> параметры подключения и список моделей.
#: `port` — порт по умолчанию, `path` — эндпоинт со списком моделей.
#: Эндпоинт чат-completions у всех совместим с OpenAI (/v1/chat/completions).
LOCAL_AI_PROVIDERS: dict[str, dict] = {
    "ollama": {
        "label": "Ollama",
        "host": "127.0.0.1",
        "port": 11434,
        "path": "/api/tags",
        "collection": "models",
        "name": "name",
        "hint": "Запуск: ollama serve",
    },
    "lmstudio": {
        "label": "LM Studio",
        "host": "127.0.0.1",
        "port": 1234,
        "path": "/v1/models",
        "collection": "data",
        "name": "id",
        "hint": "Включите Local Server во вкладке Developer",
    },
    "anythingllm": {
        "label": "AnythingLLM",
        "host": "127.0.0.1",
        "port": 3001,
        "path": "/v1/models",
        "collection": "data",
        "name": "id",
        "hint": "Включите Developer API",
    },
    "localai": {
        "label": "LocalAI",
        "host": "127.0.0.1",
        "port": 8080,
        "path": "/v1/models",
        "collection": "data",
        "name": "id",
        "hint": "Запуск: local-ai run",
    },
    "llamacpp": {
        "label": "llama.cpp server",
        "host": "127.0.0.1",
        "port": 8080,
        "path": "/v1/models",
        "collection": "data",
        "name": "id",
        "hint": "Запуск: llama-server -m model.gguf --port 8080",
    },
    "vllm": {
        "label": "vLLM",
        "host": "127.0.0.1",
        "port": 8000,
        "path": "/v1/models",
        "collection": "data",
        "name": "id",
        "hint": "Запуск: vllm serve model",
    },
    "gpt4all": {
        "label": "GPT4All",
        "host": "127.0.0.1",
        "port": 4891,
        "path": "/v1/models",
        "collection": "data",
        "name": "id",
        "hint": "Включите API-сервер в настройках",
    },
    "koboldcpp": {
        "label": "KoboldCpp",
        "host": "127.0.0.1",
        "port": 5001,
        "path": "/v1/models",
        "collection": "data",
        "name": "id",
        "hint": "Запуск: koboldcpp.py --api",
    },
    "textgenwebui": {
        "label": "text-generation-webui",
        "host": "127.0.0.1",
        "port": 5000,
        "path": "/v1/models",
        "collection": "data",
        "name": "id",
        "hint": "Запуск: ./start.sh --api",
    },
}

#: Провайдер по умолчанию, если пользователь ничего не выбрал.
DEFAULT_LOCAL_AI_PROVIDER = "ollama"

#: Путь чат-completions, совместимый с OpenAI. Одинаков для всех провайдеров.
LOCAL_AI_CHAT_PATH = "/v1/chat/completions"

POLL_DELAY_MIN = 5
POLL_DELAY_MAX = 3600
MIN_PROXY_PORT = 1
MAX_PROXY_PORT = 65535
LOGS_PAGE_SIZE = 50

REQUIRED_PACKAGES: dict[str, str] = {
    "fastapi": "fastapi>=0.110",
    "uvicorn": "uvicorn>=0.27",
    "telethon": "telethon>=1.34",
    "TelethonFakeTLS": "TelethonFakeTLS>=2022.4.4",
    "aiogram": "aiogram>=3.7.0",
    "aiohttp": "aiohttp>=3.9",
    "aiohttp_socks": "aiohttp-socks>=0.8.0",
    "dotenv": "python-dotenv>=1.0",
}

SLEEP_NO_CONFIG_S = 30
SLEEP_DB_ERROR_S = 15
SLEEP_BOT_STOPPED_S = 2


def make_post_key(source: str, post_id: int | str) -> str:
    return f"{source}:{post_id}"


def normalize_target_chat(value: str | int | None) -> str:
    """Normalize a Telegram channel ID/username/public link for Bot API sends."""
    raw = "" if value is None else str(value).strip()
    if not raw or re.fullmatch(r"-?[0-9]+", raw):
        return raw
    if raw.startswith("@"):
        if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", raw):
            return raw.lower()
        raise ValueError("Укажите корректный Telegram @username или числовой ID канала.")

    candidate = raw
    if candidate.startswith(("t.me/", "www.t.me/", "telegram.me/", "www.telegram.me/")):
        candidate = "https://" + candidate
    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        if candidate.startswith(("http://", "https://", "tg://")):
            raise ValueError("Некорректная ссылка на Telegram-канал.") from exc
        raise ValueError("Укажите корректный @username, числовой ID или ссылку t.me.") from exc

    telegram_hosts = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}
    if host not in telegram_hosts:
        if parsed.scheme or parsed.netloc:
            raise ValueError(
                "Получатель должен быть Telegram-каналом: @username, числовой ID или ссылка t.me."
            )
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", raw):
            return f"@{raw.lower()}"
        raise ValueError(
            "Получатель должен быть корректным @username, числовым ID или ссылкой t.me."
        )
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Ссылка Telegram должна начинаться с https:// или быть обычным @username.")

    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        raise ValueError("Ссылка Telegram не содержит имя или ID канала.")
    if parts[0].lower() == "s":
        parts = parts[1:]
    if not parts:
        raise ValueError("Ссылка Telegram не содержит имя канала.")

    if parts[0].startswith("+") or parts[0].lower() == "joinchat":
        raise ValueError(
            "Ссылка-приглашение Telegram не является получателем публикации. "
            "Укажите публичный @username или числовой ID канала."
        )

    # Private-channel post links use t.me/c/<channel_id>/<message_id>.
    if parts[0].lower() == "c":
        if len(parts) >= 2 and re.fullmatch(r"[0-9]+", parts[1]):
            return f"-100{parts[1]}"
        raise ValueError("В ссылке приватного канала Telegram не найден числовой ID.")

    username = parts[0]
    reserved_routes = {
        "share", "proxy", "socks", "addstickers", "addemoji", "setlanguage",
        "login", "confirmphone", "invoice", "boost", "giftcode", "addtheme",
        "bg", "iv", "joinchat", "c", "s",
    }
    if username.lower() in reserved_routes:
        raise ValueError(
            "Эта ссылка Telegram не является ссылкой на канал. Укажите @username или ID."
        )
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        return f"@{username.lower()}"
    raise ValueError("Ссылка Telegram не содержит корректное имя или ID канала.")


def runtime_label() -> str:
    return "exe" if is_frozen() else "python"


def is_truthy(value: str | int | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def warn_if_defaults() -> None:
    from database import get_db

    db = get_db()
    critical = {"tg_api_id": "TG API ID", "tg_api_hash": "TG API Hash", "bot_token": "Bot Token"}
    for key, name in critical.items():
        if not (db.get_setting(key, "") or "").strip():
            print(f"[WARN] {name} не задан — бот не запустится без этого параметра")
