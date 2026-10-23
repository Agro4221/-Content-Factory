"""Точка входа Content Factory.

Подготавливает окружение и запускает цикл чтения, обработки и публикации
постов. Аргументы ``--check`` и ``--doctor`` передают управление диагностике
окружения без запуска парсера.
"""
import sys as _sys

# Кириллица в консоли Windows: без этого печать русских сообщений
# роняет процесс на системах с кодировкой cp1252.
import console_setup as _console  # noqa: F401  (импорт сам настраивает вывод)

# python main.py --check  — самопроверка окружения без запуска парсера.
if "--check" in _sys.argv or "--doctor" in _sys.argv:
    from pathlib import Path as _Path

    _here = _Path(__file__).resolve().parent
    _doctor = None
    for _candidate in (_here / "tools" / "doctor.py", _here.parent / "tools" / "doctor.py"):
        if _candidate.is_file():
            _doctor = _candidate
            break
    if _doctor is not None:
        import runpy

        _sys.argv = [_sys.argv[0]] + [
            arg for arg in _sys.argv[1:] if arg not in ("--check", "--doctor")
        ]
        if "--install" not in _sys.argv:
            _sys.argv.append("--install")
        runpy.run_path(str(_doctor), run_name="__main__")
        raise SystemExit(0)
    print("tools/doctor.py не найден — проверка окружения недоступна.", file=_sys.stderr)
    raise SystemExit(1)

from deps import check_and_install_dependencies

check_and_install_dependencies()

# ==========================================
# ОСНОВНОЙ КОД БОТА
# ==========================================
import asyncio
import contextlib
import html
import ipaddress
import json
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import ClassVar
from urllib.parse import quote, urlsplit

import aiohttp
import uvicorn
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import (
    FSInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)
from aiogram.utils.token import TokenValidationError, validate_token
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyUnregisteredError,
    ChannelInvalidError,
    ChannelPrivateError,
    ChatForbiddenError,
    FloodWaitError,
    SessionPasswordNeededError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.custom.message import Message

import media_convert
import media_marker
import routing
import text_format
from config import (
    AI_REQUEST_TIMEOUT_S,
    AI_TEMPERATURE,
    API_HOST,
    API_PORT,
    BASE_DIR,
    BROWSER_OPEN_DELAY_S,
    BROWSER_OPEN_ON_START,
    DEFAULT_SYSTEM_PROMPT,
    MAX_PROXY_PORT,
    MEDIA_CLEANUP_INTERVAL_HOURS,
    MIN_AD_WORD_LEN,
    MIN_PROXY_PORT,
    POLL_DELAY_DEFAULT,
    POLL_DELAY_MAX,
    POLL_DELAY_MIN,
    POLL_PAUSE_S,
    REWRITE_INVENTION_LENGTH_RATIO,
    REWRITE_MIN_SHARED_WORDS,
    SESSION_PATH,
    SIGNIFICANT_WORD_MIN_LEN,
    SLEEP_API_ERROR_S,
    SLEEP_BETWEEN_POSTS_S,
    SLEEP_DB_ERROR_S,
    SLEEP_FLOOD_POLL_S,
    SLEEP_NO_CONFIG_S,
    TELEGRAM_FORMATTING_RULES,
    TEMP_DIR,
    TG_CAPTION_LIMIT,
    TG_MESSAGE_LIMIT,
    is_truthy,
    normalize_target_chat,
    warn_if_defaults,
)
from database import DatabaseWriteError, get_db
from proxy_check import (
    build_mtproto_client_kwargs,
    format_proxy_host,
    normalize_mtproto_proxy_link,
)
from Site import app
from state import bot_running

try:
    from aiohttp_socks import ProxyConnector as SocksConnector  # noqa: F401
    _HAS_SOCKS = True
except ImportError:
    _HAS_SOCKS = False

db = get_db()


# ==========================================
# УТИЛИТЫ
# ==========================================
def _is_media_skippable(media) -> bool:
    if media is None:
        return True
    cls_name = type(media).__name__
    return cls_name == "MessageMediaWebPage"


def _is_downloadable_media(media) -> bool:
    # Обычно Telethon представляет видео как MessageMediaDocument.
    # Отдельный тип оставлен для совместимых обёрток и тестовых заглушек.
    return type(media).__name__ in {
        "MessageMediaPhoto",
        "MessageMediaDocument",
        "MessageMediaVideo",
    }


def _save_skipped_group(source: str, messages: list[Message], reason: str) -> None:
    """Сохраняет идентификаторы всех сообщений логического поста одной операцией."""
    post_ids = [message.id for message in messages]
    save_group = getattr(db, "save_skipped_posts", None)
    if callable(save_group):
        save_group(source, post_ids, reason)
        return
    # Совместимость с упрощёнными адаптерами и старыми реализациями базы данных.
    for post_id in post_ids:
        db.save_skipped_post(source, post_id, reason)


class MediaDownloadError(RuntimeError):
    """A downloadable Telegram attachment was not retrieved successfully."""


def _classify_extension(path: str):
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    # Telegram's sendVideo API expects MP4. Preserve MOV/GIF/MKV/WebM and
    # unfamiliar formats as documents rather than risking a rejected publish.
    if ext == "mp4":
        return InputMediaVideo
    if ext in ("jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"):
        return InputMediaPhoto
    return InputMediaDocument


def _prepare_media_group(media_list: list) -> list:
    """Приводит альбом Telegram к совместимому с Bot API типу медиа."""
    if len(media_list) < 2:
        return media_list

    has_document = any(isinstance(item, InputMediaDocument) for item in media_list)
    has_visual = any(
        isinstance(item, (InputMediaPhoto, InputMediaVideo))
        for item in media_list
    )
    if has_document and has_visual:
        # Bot API не разрешает смешивать документы и фото/видео в одном альбоме.
        # Отправка всех элементов как документов сохраняет каждый файл.
        return [
            InputMediaDocument(media=item.media, caption=getattr(item, "caption", None))
            for item in media_list
        ]
    return media_list


def _last_safe_text_space(value: str) -> int:
    """Find the final whitespace outside an HTML tag or quoted attribute."""
    last_space = -1
    in_tag = False
    quote_char = ""
    for index, char in enumerate(value):
        if in_tag:
            if quote_char:
                if char == quote_char:
                    quote_char = ""
            elif char in {"'", '"'}:
                quote_char = char
            elif char == ">":
                in_tag = False
        elif char == "<":
            in_tag = True
        elif char.isspace():
            last_space = index
    return last_space


def _media_caption_cut_index(text: str, max_chars: int = 1020) -> int:
    """Определяет безопасную границу для подписи и продолжения текста."""
    if len(text) <= max_chars:
        return len(text)

    def raw_safe_cut(limit: int) -> int:
        prefix = text[:limit]
        space = _last_safe_text_space(prefix)
        if space > 0:
            prefix = prefix[:space]
        cut = len(prefix)

        # Не оставляем незакрытый HTML-тег или HTML-сущность в конце подписи.
        last_open = prefix.rfind("<")
        last_close = prefix.rfind(">")
        if last_open > last_close:
            cut = min(cut, last_open)
        entity_start = prefix.rfind("&")
        entity_end = prefix.rfind(";")
        if entity_start > entity_end:
            cut = min(cut, entity_start)
        return max(0, cut)

    cut = raw_safe_cut(max_chars)
    while cut > 0:
        # Закрытие HTML-тегов и экранирование текста могут увеличить длину результата.
        # Возвращаем границу исходной строки, соответствующую этому префиксу.
        rendered_prefix = _sanitize_telegram_html(text[:cut].rstrip())
        if len(rendered_prefix) <= max_chars:
            return cut
        overflow = len(rendered_prefix) - max_chars
        cut = raw_safe_cut(max(0, cut - max(1, overflow)))
    return 0


def _media_caption_split(text: str, max_chars: int = 1020) -> tuple[str, int]:
    """Возвращает корректную HTML-подпись и позицию, до которой использован исходный текст."""
    caption = (text or "").strip()
    if len(caption) <= max_chars:
        return caption, len(caption)

    cut = _media_caption_cut_index(caption, max_chars)
    while cut > 0:
        raw_prefix = caption[:cut].rstrip()
        prefix = _sanitize_telegram_html(raw_prefix)
        if len(prefix) <= max_chars:
            # Продолжение начинается с этой же границы исходного текста.
            # Санитизация фрагмента может добавить закрывающие теги.
            return prefix, cut
        cut = _media_caption_cut_index(
            caption[:cut].rstrip(),
            max(1, cut - max(8, len(prefix) - max_chars + 8)),
        )
    return "", 0


def _media_caption_prefix(text: str, max_chars: int = 1020) -> str:
    """Return valid, balanced HTML that fits alongside an ellipsis in Telegram."""
    prefix, _cut = _media_caption_split(text, max_chars)
    return prefix


def _media_caption_remainder(text: str) -> str:
    """Return valid text omitted from Telegram's media-caption limit."""
    caption = (text or "").strip()
    if len(caption) <= 1024:
        return ""
    _prefix, cut = _media_caption_split(caption, 1020)
    return _sanitize_telegram_html(caption[cut:].lstrip())


def _progress_bar(current: int, total: int) -> None:
    if not total or total <= 0:
        print(f"\r--> [DOWNLOAD] {current/1024/1024:.1f}MB", end="", flush=True)
        return
    percent = (current / total) * 100
    print(
        f"\r--> [DOWNLOAD] {current/1024/1024:.1f}MB / {total/1024/1024:.1f}MB ({percent:.1f}%)",
        end="",
        flush=True,
    )


def _contains_ad(text: str, words: list[str]) -> bool:
    if not words:
        return False
    lowered = text.lower()
    for w in words:
        if len(w) < MIN_AD_WORD_LEN:
            continue
        if re.search(r"\b" + re.escape(w.lower()) + r"\b", lowered):
            return True
    return False


def _meaningful_text_length(text: str) -> int:
    """Число содержательных символов в тексте.

    Считаются только буквы и цифры: эмодзи, пробелы и пунктуация не дают
    материала для пересказа, но делают подпись длинной.
    """
    return sum(1 for ch in text if ch.isalnum())


#: Сколько раз повторять отправку, если не удалось подключиться к Telegram.
SEND_CONNECT_RETRIES = 3

#: Пауза перед первой повторной попыткой отправки, секунды.
SEND_RETRY_DELAY_S = 5


def _is_connect_failure(exc: BaseException) -> bool:
    """Не удалось подключиться — отправку можно повторить безопасно.

    Повторяем только ошибки соединения: запрос в этом случае точно не
    дошёл до Telegram. Ошибки по таймауту не повторяем — там запрос
    мог дойти, и повтор продублировал бы сообщение.
    """
    if isinstance(exc, (aiohttp.ClientConnectorError, aiohttp.ClientProxyConnectionError)):
        return True
    if isinstance(exc, TelegramNetworkError):
        inner = getattr(exc, "__cause__", None) or getattr(exc, "original", None)
        if inner is not None:
            return _is_connect_failure(inner)
    return False


async def _send_with_retry(bot, method: str, **kwargs) -> None:
    """Отправляет сообщение, повторяя при обрыве соединения.

    Прямое подключение к Bot API у провайдеров часто обрывается, и без
    повтора пост ждал бы следующего прохода цикла — минуты. Повторяем
    только неудачные подключения, они безопасны.
    """
    delay = SEND_RETRY_DELAY_S
    for attempt in range(1, SEND_CONNECT_RETRIES + 1):
        try:
            await getattr(bot, method)(**kwargs)
            return
        except Exception as exc:
            if attempt >= SEND_CONNECT_RETRIES or not _is_connect_failure(exc):
                raise
            print(
                f"!!! Отправка не прошла ({type(exc).__name__}), "
                f"повтор {attempt}/{SEND_CONNECT_RETRIES - 1} через {delay} с"
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)


def _ai_timeout_seconds(raw: object = None) -> int:
    """Таймаут запроса к ИИ в секундах (настройка панели или .env)."""
    if raw is None:
        raw = db.get_setting("ai_timeout", "")
    if raw in (None, ""):
        return AI_REQUEST_TIMEOUT_S
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return AI_REQUEST_TIMEOUT_S
    if value < 10:
        return AI_REQUEST_TIMEOUT_S
    return min(value, 3600)

def _significant_words(text: str) -> set[str]:
    """Значимые слова текста: буквы длиной от 4 символов.

    Эмодзи, числа и короткие служебные слова (��, и, на) не входят: по ним
    нельзя понять, о чём пост.
    """
    return set(
        re.findall(
            rf"[^\W\d_]{{{SIGNIFICANT_WORD_MIN_LEN},}}",
            text.lower(),
            re.UNICODE,
        )
    )


def _rewrite_is_about_original(original: str, rewritten: str) -> bool:
    """Проверяет, что пересказ относится к исходному тексту.

    Лёгкая модель иногда сочиняет новость целиком: из короткой подписи
    «One, two, three» получается «шторм обрушился на Приморский край». Такая
    «новость» уезжает в канал вместе с картинкой исходного поста, и подпись
    перестаёт соответствовать изображению. Если ни одного значимого слова
    исходника не попало в пересказ — пересказ выдуман, и публиковать его
    нельзя.
    """
    if REWRITE_MIN_SHARED_WORDS <= 0:
        return True
    source_words = _significant_words(original)
    if not source_words:
        # Переписывать нечего — это разбирается отдельно, до запроса к ИИ.
        return True
    rewritten_words = _significant_words(rewritten)
    if len(source_words & rewritten_words) >= REWRITE_MIN_SHARED_WORDS:
        return True
    # Общих слов нет. Пересказ считается выдумкой, только если он
    # заметно длиннее оригинала: выдуманная новость всегда длиннее
    # подписи, из которой её сочинили.
    return len(rewritten) < len(original) * REWRITE_INVENTION_LENGTH_RATIO


class ProxyConfigurationError(RuntimeError):
    """Enabled proxy settings cannot be applied safely."""


@dataclass(frozen=True)
class ProxySetup:
    """Как именно применяется включённый прокси.

    Прокси в проекте не один: разные виды трафика уходят разными
    протоколами, и не каждый вид прокси умеет их все.

    * ``telethon_proxy`` — параметры для TelegramClient (чтение каналов).
    * ``mtproto_kwargs`` — параметры для TelegramClient в режиме MTProto.
    * ``bot_session`` — сессия aiogram для Bot API (публикация).
    * ``ai_proxy_url`` — адрес прокси для запросов к ИИ. ``None`` означает,
      что эти запросы идут напрямую (MTProxy не умеет HTTP).
    * ``ai_proxy_kind`` — ``"http"``, ``"socks"`` или ``None``.
    * ``covers_all_traffic`` — True, если через прокси уходит весь внешний
      трафик. Панель показывает это пользователю.
    """

    telethon_proxy: dict | None = None
    mtproto_kwargs: dict | None = None
    bot_session: AiohttpSession | None = None
    ai_proxy_url: str | None = None
    ai_proxy_kind: str | None = None

    @property
    def covers_all_traffic(self) -> bool:
        """True, если через прокси идёт и трафик к ИИ, и трафик Telegram."""
        return self.ai_proxy_url is not None and self.mtproto_kwargs is None


def _make_socks_session(cfg: dict) -> AiohttpSession:
    if not _HAS_SOCKS:
        raise ProxyConfigurationError(
            "Для SOCKS нужен пакет aiohttp-socks; запуск без прокси запрещён."
        )

    proxy_type = (cfg.get("proxy_type") or "socks5").strip().lower()
    if proxy_type not in ("socks5", "socks4"):
        raise ProxyConfigurationError(f"Неподдерживаемый тип SOCKS-прокси: {proxy_type!r}")

    host = (cfg.get("proxy_host") or "").strip()
    port = _validate_port(str(cfg.get("proxy_port") or ""))
    if not host or port is None:
        raise ProxyConfigurationError("Для SOCKS-прокси нужны корректные host и port.")

    user = cfg.get("proxy_user") or ""
    password = cfg.get("proxy_pass") or ""
    auth = (
        f"{quote(str(user), safe='')}:{quote(str(password), safe='')}@"
        if (user or password)
        else ""
    )

    url_host = format_proxy_host(host)
    proxy_url = f"{proxy_type}://{auth}{url_host}:{port}"
    try:
        return AiohttpSession(proxy=proxy_url)
    except Exception as exc:
        raise ProxyConfigurationError(
            f"Не удалось инициализировать SOCKS-сессию: {exc}"
        ) from exc


def _validate_port(port_str: str) -> int | None:
    if (
        not port_str
        or not port_str.isascii()
        or not port_str.isdigit()
        or len(port_str) > len(str(MAX_PROXY_PORT))
    ):
        return None
    p = int(port_str)
    return p if MIN_PROXY_PORT <= p <= MAX_PROXY_PORT else None


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _source_entity(source: str) -> str | int:
    """Pass numeric Telegram peer IDs to Telethon as integers, not phone-like strings."""
    normalized = str(source).strip()
    if re.fullmatch(r"-?[0-9]+", normalized):
        return int(normalized)
    return normalized


def _target_chat_id(value: str | int) -> str | int:
    """Bot API accepts numeric chat IDs as integers and public usernames as strings."""
    normalized = str(value).strip()
    if re.fullmatch(r"-?[0-9]+", normalized):
        return int(normalized)
    return normalized


def _is_valid_bot_token_format(token: str) -> bool:
    try:
        validate_token(token)
    except TokenValidationError:
        return False
    return True

# ==========================================
# ЗАГРУЗКА КОНФИГА
# ==========================================
def _load_config() -> dict:
    get_all_settings = getattr(db, "get_all_settings", None)
    snapshot = get_all_settings() if callable(get_all_settings) else None

    def get_setting(key: str, default=""):
        if snapshot is not None:
            return snapshot.get(key, default)
        # Compatibility for lightweight adapters/test doubles.
        return db.get_setting(key, default)

    ai_mode = (get_setting("ai_mode", "LOCAL") or "LOCAL").strip().upper()
    if ai_mode not in {"LOCAL", "REMOTE"}:
        raise ValueError(
            f"Некорректный ai_mode={ai_mode!r}; допустимы только LOCAL и REMOTE."
        )

    local_host = (get_setting("local_host", "127.0.0.1") or "127.0.0.1").strip()
    local_port_raw = str(get_setting("local_port", "1234") or "1234").strip()
    local_port = _validate_port(local_port_raw)
    if not local_host:
        raise ValueError("local_host не может быть пустым.")
    if any(char in local_host for char in "/?#@") or any(
        char.isspace() for char in local_host
    ):
        raise ValueError(
            "local_host должен быть именем хоста или IP-адресом, без URL-схемы и пути."
        )
    bracketed_host = local_host.startswith("[") or local_host.endswith("]")
    host_value = (
        local_host[1:-1]
        if local_host.startswith("[") and local_host.endswith("]")
        else local_host
    )
    if bracketed_host:
        try:
            if ipaddress.ip_address(host_value).version != 6:
                raise ValueError("brackets are only valid for IPv6")
        except ValueError as exc:
            raise ValueError(
                "local_host в квадратных скобках должен быть корректным IPv6-адресом."
            ) from exc
    else:
        try:
            ipaddress.ip_address(host_value)
        except ValueError as exc:
            if ":" in host_value or not re.fullmatch(r"[A-Za-z0-9.-]+", host_value):
                raise ValueError(
                    "local_host должен быть корректным именем хоста или IP-адресом."
                ) from exc
    if local_port is None:
        raise ValueError("local_port должен быть целым числом от 1 до 65535.")

    remote_url = (get_setting("remote_url", "") or "").strip()
    if ai_mode == "REMOTE":
        try:
            parsed_remote_url = urlsplit(remote_url)
            remote_port = parsed_remote_url.port
            valid_remote_port = remote_port is None or 1 <= remote_port <= 65535
        except ValueError as exc:
            raise ValueError(
                "remote_url содержит некорректный адрес или порт."
            ) from exc
        remote_scheme = parsed_remote_url.scheme.lower()
        remote_host = parsed_remote_url.hostname
        http_loopback = False
        if remote_host:
            if remote_host.lower() == "localhost":
                http_loopback = True
            else:
                try:
                    http_loopback = ipaddress.ip_address(remote_host).is_loopback
                except ValueError:
                    http_loopback = False
        if (
            remote_scheme not in {"http", "https"}
            or not remote_host
            or not valid_remote_port
            or (remote_scheme == "http" and not http_loopback)
        ):
            raise ValueError(
                "remote_url для ai_mode=REMOTE должен использовать HTTPS; HTTP разрешён "
                "только для localhost или loopback-адреса."
            )

    sources_raw = get_setting("sources", "")
    sources: list[str] = []
    if sources_raw:
        for raw_source in sources_raw.split("\n"):
            raw_source = raw_source.strip()
            if not raw_source:
                continue
            try:
                source = normalize_target_chat(raw_source)
            except ValueError as exc:
                raise ValueError(
                    f"Некорректный источник Telegram {raw_source!r}: {exc}"
                ) from exc
            if source and source not in sources:
                sources.append(source)

    ads_raw = get_setting("ads_words", "")
    ads_words = [w.strip() for w in ads_raw.split("\n") if w.strip()] if ads_raw else []

    # Маршруты: канал → список тегов. Старый формат «категория → канал»
    # читается как маршрут «канал → [категория]», поэтому старые настройки
    # продолжают работать без ручного переноса.
    raw_routes = get_setting("tag_routes", "") or ""
    if not raw_routes.strip():
        raw_routes = get_setting("topic_channels", "{}") or "{}"
    try:
        tag_routes = routing.parse_routes(raw_routes)
    except ValueError as exc:
        raise ValueError(
            "Маршруты (tag_routes / topic_channels) содержат повреждённый JSON; "
            "настройки не применены, чтобы не отправлять посты в неверный канал."
        ) from exc

    normalized_routes: dict[str, list[str]] = {}
    for channel, tags in tag_routes.items():
        try:
            norm_chat = normalize_target_chat(str(channel).strip())
        except ValueError as exc:
            raise ValueError(
                f"Некорректный целевой канал в маршрутах ({channel!r}): {exc}"
            ) from exc
        if norm_chat:
            normalized_routes[norm_chat] = list(tags)
    tag_routes = normalized_routes

    # Старые настройки остаются в базе, но больше не используются: категории
    # заменены тегами.
    topic_channels = {tag: channel for channel, tags in tag_routes.items() for tag in tags[:1]}

    poll_delay_raw = str(
        get_setting("poll_delay", str(POLL_DELAY_DEFAULT)) or ""
    ).strip()
    if not poll_delay_raw:
        poll_delay = POLL_DELAY_DEFAULT
    elif (
        not poll_delay_raw.isascii()
        or not poll_delay_raw.isdigit()
        or len(poll_delay_raw) > len(str(POLL_DELAY_MAX))
        or not POLL_DELAY_MIN <= int(poll_delay_raw) <= POLL_DELAY_MAX
    ):
        raise ValueError(
            f"poll_delay должен быть целым числом от {POLL_DELAY_MIN} до {POLL_DELAY_MAX}."
        )
    else:
        poll_delay = int(poll_delay_raw)

    return {
        "api_id": _safe_int(get_setting("tg_api_id", "0"), 0),
        "api_hash": (get_setting("tg_api_hash", "") or "").strip(),
        "bot_token": (get_setting("bot_token", "") or "").strip(),
        "ai_mode": ai_mode,
        "local_url": (
            f"http://{format_proxy_host(local_host)}:"
            f"{local_port}/v1/chat/completions"
        ),
        "local_model": get_setting("local_model", ""),
        "remote_url": remote_url,
        "remote_api_key": get_setting("remote_api_key", ""),
        "remote_model": get_setting("remote_model", ""),
        "sources": sources,
        "topic_channels": topic_channels,
        "tag_routes": tag_routes,
        "tag_routes_tags": routing.all_tags(tag_routes),
        "ads_words": ads_words,
        "poll_delay": poll_delay,
        "use_proxy": is_truthy(get_setting("use_proxy", "0")),
        "proxy_type": (get_setting("proxy_type", "") or "").strip().lower(),
        "proxy_host": (get_setting("proxy_host", "") or "").strip(),
        "proxy_port": (get_setting("proxy_port", "") or "").strip(),
        "proxy_user": get_setting("proxy_user", ""),
        "proxy_pass": get_setting("proxy_pass", ""),
        "mtproto_key": get_setting("mtproto_key", ""),
        "publish_mode": (get_setting("publish_mode", "auto") or "auto").strip().lower(),
        "watermark": get_setting("watermark_text", ""),
        "ai_timeout": _ai_timeout_seconds(get_setting("ai_timeout", "")),
    }


def _setup_proxy(cfg: dict) -> ProxySetup:
    if not cfg["use_proxy"]:
        return ProxySetup()

    try:
        cfg = normalize_mtproto_proxy_link(cfg)
    except ValueError as exc:
        raise ProxyConfigurationError(f"Некорректная MTProto-ссылка: {exc}") from exc

    host = (cfg.get("proxy_host") or "").strip()
    if not host:
        raise ProxyConfigurationError(
            "Прокси включён, но proxy_host пуст. Прямое подключение не будет использоваться."
        )

    port = _validate_port(str(cfg.get("proxy_port") or ""))
    if port is None:
        raise ProxyConfigurationError(
            f"Прокси включён, но proxy_port некорректен: {cfg.get('proxy_port')!r}. "
            "Прямое подключение не будет использоваться."
        )

    proxy_type = (cfg.get("proxy_type") or "").strip().lower()
    mtproto_key = (cfg.get("mtproto_key") or "").strip()
    # Явно выбранный тип прокси имеет приоритет. Определяем MTProto по ключу
    # только для старых настроек, в которых proxy_type не задан.
    if not proxy_type:
        proxy_type = "mtproto" if mtproto_key else "http"

    if proxy_type == "mtproto":
        if not mtproto_key:
            raise ProxyConfigurationError(
                "Выбран MTProto, но секретный ключ не задан. Прямое подключение не будет использоваться."
            )
        try:
            mtproto_kwargs = build_mtproto_client_kwargs(host, port, mtproto_key)
        except (ValueError, ImportError) as exc:
            raise ProxyConfigurationError(
                f"Секрет MTProto некорректен или зависимость отсутствует: {exc}. Прямое подключение не будет использоваться."
            ) from exc
        db.add_log(
            "WARN-300",
            "MTProto-прокси применяется только для чтения каналов; бот-отправщик "
            "и запросы к ИИ подключаются напрямую.",
        )
        return ProxySetup(mtproto_kwargs=mtproto_kwargs)
    if proxy_type not in ("http", "socks5", "socks4"):
        raise ProxyConfigurationError(
            f"Неподдерживаемый тип прокси: {proxy_type!r}. Прямое подключение не будет использоваться."
        )

    telethon_proxy = {
        "proxy_type": proxy_type,
        "addr": host,
        "port": port,
        "username": cfg.get("proxy_user") or None,
        "password": cfg.get("proxy_pass") or None,
    }
    if proxy_type == "http":
        proxy_host = format_proxy_host(host)
        if cfg.get("proxy_user") or cfg.get("proxy_pass"):
            proxy_user = quote(str(cfg.get("proxy_user") or ""), safe="")
            proxy_password = quote(str(cfg.get("proxy_pass") or ""), safe="")
            proxy_url = f"http://{proxy_user}:{proxy_password}@{proxy_host}:{port}"
        else:
            proxy_url = f"http://{proxy_host}:{port}"
        try:
            bot_session = AiohttpSession(proxy=proxy_url)
        except Exception as exc:
            raise ProxyConfigurationError(
                f"Не удалось инициализировать HTTP-прокси: {exc}. Прямое подключение не будет использоваться."
            ) from exc
        return ProxySetup(
            telethon_proxy=telethon_proxy,
            bot_session=bot_session,
            ai_proxy_url=proxy_url,
            ai_proxy_kind="http",
        )

    bot_session = _make_socks_session(cfg)
    return ProxySetup(
        telethon_proxy=telethon_proxy,
        bot_session=bot_session,
        ai_proxy_url=_socks_proxy_url(cfg),
        ai_proxy_kind="socks",
    )


def _socks_proxy_url(cfg: dict) -> str:
    """Адрес SOCKS-прокси в виде URL для клиента aiohttp."""
    proxy_type = (cfg.get("proxy_type") or "socks5").strip().lower()
    host = (cfg.get("proxy_host") or "").strip()
    port = _validate_port(str(cfg.get("proxy_port") or ""))
    if port is None:
        raise ProxyConfigurationError("Для SOCKS-прокси нужны корректные host и port.")
    user = cfg.get("proxy_user") or ""
    password = cfg.get("proxy_pass") or ""
    auth = f"{quote(str(user), safe='')}:{quote(str(password), safe='')}@" if (user or password) else ""
    url_host = format_proxy_host(host)
    return f"{proxy_type}://{auth}{url_host}:{port}"


def _make_ai_session(proxy: ProxySetup, timeout: int) -> aiohttp.ClientSession:
    """Сессия для запросов к ИИ.

    Через прокси уходит весь внешний трафик, если прокси это умеет
    (HTTP или SOCKS). Для MTProxy это невозможно: протокол MTProxy
    пропускает только Telegram-трафик, обычный HTTPS через него не идёт.
    """
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    if not proxy.ai_proxy_url:
        return aiohttp.ClientSession(timeout=client_timeout)

    if proxy.ai_proxy_kind == "http":
        return aiohttp.ClientSession(timeout=client_timeout, proxy=proxy.ai_proxy_url)

    # SOCKS: aiohttp сам по себе SOCKS не поддерживает, нужен коннектор
    # из aiohttp-socks — того же пакета, что используется для Bot API.
    if not _HAS_SOCKS:
        raise ProxyConfigurationError(
            "Для SOCKS-прокси нужен пакет aiohttp-socks; запросы к ИИ не пойдут через прокси."
        )
    try:
        connector = SocksConnector.from_url(proxy.ai_proxy_url)
    except Exception as exc:
        raise ProxyConfigurationError(
            f"Не удалось инициализировать SOCKS-подключение для ИИ: {exc}"
        ) from exc
    return aiohttp.ClientSession(timeout=client_timeout, connector=connector)

# ==========================================
# ОБРАБОТКА ПОСТОВ
# ==========================================
class _TelegramHTMLSanitizer(HTMLParser):
    """Keep only Telegram-supported HTML and always emit balanced, escaped markup."""

    _ALLOWED_TAGS: ClassVar[set[str]] = {
        "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
        "code", "pre", "a", "blockquote", "tg-spoiler", "tg-emoji",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []

    @staticmethod
    def _safe_href(value: str) -> bool:
        from urllib.parse import urlsplit

        try:
            scheme = urlsplit(value).scheme.lower()
        except ValueError:
            return False
        return scheme in {"http", "https", "tg", "mailto"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in self._ALLOWED_TAGS:
            return

        attributes = dict(attrs)
        rendered = True
        attr_text = ""
        if tag == "a":
            href = attributes.get("href") or ""
            if not self._safe_href(href):
                rendered = False
            else:
                attr_text = f' href="{html.escape(href, quote=True)}"'
        elif tag == "code":
            language = attributes.get("class") or ""
            if re.fullmatch(r"language-[A-Za-z0-9_+-]{1,40}", language):
                attr_text = f' class="{html.escape(language, quote=True)}"'
        elif tag == "tg-emoji":
            emoji_id = attributes.get("emoji-id") or ""
            if re.fullmatch(r"[0-9]+", emoji_id):
                attr_text = f' emoji-id="{emoji_id}"'
            else:
                rendered = False
        elif tag == "blockquote" and "expandable" in attributes:
            attr_text = " expandable"

        self.stack.append((tag, rendered))
        if rendered:
            self.parts.append(f"<{tag}{attr_text}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        matching_index = next(
            (index for index in range(len(self.stack) - 1, -1, -1)
             if self.stack[index][0] == tag),
            None,
        )
        if matching_index is None:
            return
        while len(self.stack) > matching_index:
            open_tag, rendered = self.stack.pop()
            if rendered:
                self.parts.append(f"</{open_tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(html.escape(data, quote=False))

    def handle_comment(self, data: str) -> None:
        # Формат HTML в Telegram не поддерживает комментарии.
        return

    def result(self) -> str:
        while self.stack:
            tag, rendered = self.stack.pop()
            if rendered:
                self.parts.append(f"</{tag}>")
        return "".join(self.parts)


def _sanitize_telegram_html(text: str) -> str:
    """Сохраняет поддерживаемое Telegram-форматирование и исправляет некорректный HTML."""
    parser = _TelegramHTMLSanitizer()
    try:
        parser.feed(text or "")
        parser.close()
    except Exception:
        # Некорректный HTML не должен останавливать цикл публикации.
        return html.escape(text or "", quote=False)
    return parser.result()


def _truncate_telegram_text(text: str, max_length: int = 4096) -> str:
    """Обрезает текст, сохраняя корректный HTML Telegram и ограничение длины."""
    marker = "\n\n[…обрезано…]"
    if len(text) <= max_length:
        return text
    if max_length <= len(marker):
        return marker[-max_length:] if max_length > 0 else ""

    content_limit = max_length - len(marker)
    cut = _media_caption_cut_index(text, content_limit)
    while cut > 0:
        raw_prefix = text[:cut].rstrip()
        prefix = _sanitize_telegram_html(raw_prefix)
        if len(prefix) <= content_limit:
            result = prefix + marker
            if len(result) <= max_length and _sanitize_telegram_html(result) == result:
                return result
        # Закрывающие теги и экранирование сущности могут увеличить длину.
        # Уменьшаем исходный фрагмент и повторяем проверку.
        next_limit = max(1, cut - max(8, len(prefix) - content_limit + 8))
        cut = _media_caption_cut_index(text[:cut], next_limit)

    return marker


def _queue_media_dir() -> str:
    """Каталог для медиафайлов очереди постов."""
    return os.path.join(BASE_DIR, "queue_media")


def _remove_stale_queue_media(raw_media_files: object, keep: list[str]) -> None:
    """Удаляет копии медиа, на которые больше не ссылается очередь.

    При повторной постановке поста в очередь база получает новый список
    файлов, а прежние копии остаются в queue_media навсегда — диск растёт,
    а в панели они не видны.
    """
    if not raw_media_files:
        return
    try:
        previous = json.loads(raw_media_files) if isinstance(raw_media_files, str) else []
    except (TypeError, ValueError):
        return
    if not isinstance(previous, list):
        return

    keep_set = {str(name) for name in keep}
    queue_dir = _queue_media_dir()
    for name in previous:
        file_name = str(name or "")
        if not file_name or file_name in keep_set:
            continue
        safe_name = os.path.basename(file_name.replace("\\", "/"))
        if safe_name != file_name:
            continue
        with contextlib.suppress(OSError):
            os.remove(os.path.join(queue_dir, safe_name))


#: Момент последней автоочистки медиа (монотонное время, не зависит от часов).
_last_media_cleanup_ts = 0.0

#: Сколько раз пробуем обработать пост, прежде чем признать его битым.
MAX_POST_ATTEMPTS = 5

#: Сколько раз пробуем опубликовать отложенный пост, прежде чем снять его.
MAX_PUBLISH_ATTEMPTS = 5

#: Счётчик попыток по постам, которые не удалось обработать. Нужен, чтобы
#: один битый пост не блокировал всю ленту источника навсегда.
_POISON_ATTEMPTS: dict[str, int] = {}


def _save_post_to_queue(
    source: str,
    messages: list[Message],
    publish_text: str,
    target_chats: list[str | int],
    category: str,
    media_list: list,
    temp_paths: list[str],
) -> int:
    """Сохраняет пост в очередь ручной публикации вместе с медиа."""
    os.makedirs(_queue_media_dir(), exist_ok=True)
    marker = media_marker.media_marker(source, messages[0].id)
    media_files: list[str] = []
    media_kind = ""
    for path in temp_paths:
        if not path or not os.path.isfile(path):
            continue
        try:
            # Уникальное имя не даёт разным постам перезаписать один файл.
            # В имени есть метка поста: по ней видно, чьё это медиа.
            ext = os.path.splitext(os.path.basename(path))[1]
            name = media_marker.media_file_name(
                marker, int(time.time() * 1000), len(media_files), ext
            )
            target_path = os.path.join(_queue_media_dir(), name)
            shutil.copy2(path, target_path)
            media_files.append(name)
        except OSError as exc:
            db.add_log("WARN-511", f"Медиафайл не сохранён в очереди: {exc}")
            continue

    if media_list:
        first = media_list[0]
        if isinstance(first, InputMediaPhoto):
            media_kind = "photo"
        elif isinstance(first, InputMediaVideo):
            media_kind = "video"
        else:
            media_kind = "document"

    original_text = "\n".join(m.message for m in messages if m.message).strip()
    group_id = ""
    for message in messages:
        candidate = getattr(message, "grouped_id", None)
        if candidate:
            group_id = str(candidate)
            break

    # Прежние копии медиа этого поста больше не нужны: база сейчас получит
    # новый список файлов, а старые иначе остались бы в queue_media навсегда.
    previous = None
    getter = getattr(db, "get_queued_post_by_source", None)
    if callable(getter):
        try:
            previous = getter(source, messages[0].id)
        except Exception:
            previous = None
    if previous:
        _remove_stale_queue_media(previous.get("media_files"), media_files)

    chats = [str(chat).strip() for chat in target_chats if str(chat).strip()]

    return db.save_post_to_queue(
        {
            "source_name": source,
            "post_id": str(messages[0].id),
            "group_id": group_id,
            "original_text": original_text,
            "publish_text": publish_text,
            "media_files": json.dumps(media_files, ensure_ascii=False),
            "media_kind": media_kind,
            "category": (category or "").strip().lower(),
            "target_chat": chats[0] if chats else "",
            "target_chats": json.dumps(chats, ensure_ascii=False),
        }
    )


def _queued_target_chats(post: dict) -> list[str]:
    """Каналы, в которые нужно опубликовать пост из очереди.

    Пост может уйти сразу в несколько каналов: теги совпали с маршрутами
    нескольких каналов. Старая колонка target_chat остаётся основным
    каналом — по ней видно, куда пост ушёл.
    """
    chats: list[str] = []
    raw_list = post.get("target_chats") or "[]"
    try:
        parsed = json.loads(raw_list) if isinstance(raw_list, str) else raw_list
    except (TypeError, ValueError):
        parsed = []
    if isinstance(parsed, list):
        for item in parsed:
            chat = str(item or "").strip()
            if chat and chat not in chats:
                chats.append(chat)
    main_chat = str(post.get("target_chat") or "").strip()
    if main_chat and main_chat not in chats:
        chats.append(main_chat)
    return chats


async def publish_queued_post(bot: Bot, post: dict) -> tuple[bool, str]:
    """Публикует пост из очереди. Возвращает (успех, сообщение)."""
    target_chats = _queued_target_chats(post)
    if not target_chats:
        return False, "Не указан канал назначения."

    text = str(post.get("publish_text") or "")
    # Текст поста мог править пользователь в панели: он может содержать
    # сырые теги или быть длиннее, чем Telegram принимает.
    text = _sanitize_telegram_html(text)
    if len(text) > TG_MESSAGE_LIMIT:
        original_len = len(text)
        text = _truncate_telegram_text(text, TG_MESSAGE_LIMIT)
        db.add_log(
            "WARN-500",
            f"Текст поста обрезан с {original_len} до {len(text)} символов для Telegram.",
        )
    try:
        media_files = json.loads(post.get("media_files") or "[]")
    except (TypeError, ValueError):
        media_files = []
    if not isinstance(media_files, list):
        media_files = []

    media_dir = _queue_media_dir()
    expected_marker = media_marker.media_marker(
        str(post.get("source_name") or ""), str(post.get("post_id") or "")
    )
    paths = []
    for name in media_files:
        safe_name = os.path.basename(str(name).replace("\\", "/"))
        if not safe_name or safe_name != str(name):
            continue
        if not media_marker.belongs_to_post(safe_name, expected_marker):
            # Файл помечен как чужой: публиковать его вместе с этим постом
            # нельзя, иначе картинка окажется не от того поста.
            db.add_log(
                "WARN-512",
                f"Медиафайл {safe_name} принадлежит другому посту; "
                f"в публикации {post.get('id')} пропущен.",
            )
            continue
        full = os.path.join(media_dir, safe_name)
        if os.path.isfile(full):
            paths.append(full)

    try:
        if not paths:
            if not text:
                return False, "В посте нет ни текста, ни медиа."
            for target_chat in target_chats:
                await _send_with_retry(
                    bot, "send_message", chat_id=target_chat, text=text
                )
        elif len(paths) == 1:
            kind = str(post.get("media_kind") or "document")
            # Текст длиннее подписи уходит отдельным сообщением, но не
            # делится надвое.
            fits_caption = len(text) <= TG_CAPTION_LIMIT
            caption = text if fits_caption else None
            for target_chat in target_chats:
                if kind == "photo":
                    await _send_with_retry(
                        bot,
                        "send_photo",
                        chat_id=target_chat,
                        photo=FSInputFile(paths[0], filename=media_marker.clean_filename(paths[0])),
                        caption=caption or None,
                    )
                elif kind == "video":
                    await _send_with_retry(
                        bot,
                        "send_video",
                        chat_id=target_chat,
                        video=FSInputFile(paths[0], filename=media_marker.clean_filename(paths[0])),
                        caption=caption or None,
                    )
                else:
                    await _send_with_retry(
                        bot,
                        "send_document",
                        chat_id=target_chat,
                        document=FSInputFile(paths[0], filename=media_marker.clean_filename(paths[0])),
                        caption=caption or None,
                    )
                if text and not fits_caption:
                    await _send_with_retry(
                        bot, "send_message", chat_id=target_chat, text=text
                    )
        else:
            # Подпись альбома ограничена 1024 символами, а обычное
            # сообщение вмещает 4096. Длинный текст уходит отдельным
            # сообщением — иначе Telegram отклоняет подпись целиком и
            # пост не публикуется никогда.
            fits_caption = len(text) <= TG_CAPTION_LIMIT
            caption = text if fits_caption else None
            for target_chat in target_chats:
                media = _build_album_media(paths, caption)
                try:
                    await _send_with_retry(
                        bot, "send_media_group", chat_id=target_chat, media=media
                    )
                except Exception:
                    # Альбом из файлов одного вида Telegram принимает не всегда.
                    # Откатываемся к документам — они принимаются всегда.
                    media = [
                        InputMediaDocument(
                            media=FSInputFile(path, filename=media_marker.clean_filename(path)),
                            caption=caption or None,
                        )
                        for path in paths
                    ]
                    await _send_with_retry(
                        bot, "send_media_group", chat_id=target_chat, media=media
                    )
                if text and not fits_caption:
                    await _send_with_retry(
                        bot, "send_message", chat_id=target_chat, text=text
                    )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "Опубликовано."


def _build_album_media(paths: list[str], caption: str | None) -> list:
    """Собирает медиа для альбома: фото или видео, если все файлы одного вида.

    Telegram принимает в альбоме только файлы одного вида — смешанный
    альбом он отклоняет. Подпись можно указать лишь к первому элементу,
    поэтому она ставится только туда.
    """
    extensions = {media_convert.file_extension(path) for path in paths}
    if extensions and extensions.issubset(media_convert.IMAGE_EXTENSIONS):
        return [
            InputMediaPhoto(
                media=FSInputFile(path, filename=media_marker.clean_filename(path)),
                caption=caption if index == 0 else None
            )
            for index, path in enumerate(paths)
        ]
    if extensions and extensions.issubset(media_convert.NATIVE_VIDEO_EXTENSIONS):
        return [
            InputMediaVideo(
                media=FSInputFile(path, filename=media_marker.clean_filename(path)),
                caption=caption if index == 0 else None
            )
            for index, path in enumerate(paths)
        ]
    return [
        InputMediaDocument(media=FSInputFile(path, filename=media_marker.clean_filename(path)),
                caption=caption if index == 0 else None)
        for index, path in enumerate(paths)
    ]


async def _cleanup_expired_media() -> int:
    """Удаляет медиа опубликованных постов старше срока хранения.

    Возвращает количество удалённых файлов.
    """
    settings = db.get_all_settings()
    if str(settings.get("media_cleanup_enabled", "1")).strip() not in {"1", "true", "yes", "on"}:
        return 0
    try:
        retention = int(str(settings.get("media_retention_days", "3") or "0").strip() or 0)
    except (TypeError, ValueError):
        retention = 0
    if retention <= 0:
        return 0

    media_dir = _queue_media_dir()
    removed = 0
    for post in db.published_posts_with_media(retention):
        for name in post["media_files"]:
            safe_name = os.path.basename(str(name).replace("\\", "/"))
            if not safe_name or safe_name != str(name):
                continue
            full = os.path.join(media_dir, safe_name)
            try:
                if os.path.isfile(full):
                    os.remove(full)
                    removed += 1
            except OSError:
                continue
        db.clear_post_media(post["id"])
    if removed:
        db.add_log("OK-513", f"Автоочистка медиа: удалено файлов {removed} (срок хранения {retention} дн.).")
    return removed


async def _publish_due_scheduled_posts(bot: Bot) -> int:
    """Публикует отложенные посты, у которых наступило время."""
    published = 0
    for post in db.due_scheduled_posts(limit=10):
        ok, message = await publish_queued_post(bot, post)
        if ok:
            db.update_queued_post(
                post["id"],
                {"status": "published", "published_at": time.strftime("%Y-%m-%d %H:%M:%S"), "last_error": ""},
            )
            db.add_log("OK-511", f"Отложенный пост {post['id']} опубликован в {post.get('target_chat')}.")
            published += 1
        else:
            # Пост, который не публикуется из-за постоянной ошибки, не
            # должен повторяться вечно: после нескольких попыток он
            # переводится в «ошибка» и перестаёт занимать очередь.
            attempts = _post_publish_attempts(post) + 1
            changes: dict = {"last_error": message, "publish_attempts": attempts}
            if attempts >= MAX_PUBLISH_ATTEMPTS:
                changes["status"] = "failed"
            db.update_queued_post(post["id"], changes)
            if changes.get("status") == "failed":
                db.add_log(
                    "ERR-511",
                    f"Отложенный пост {post['id']} не опубликован после {attempts} попыток: "
                    f"{message}. Пост помечен как ошибочный.",
                )
            else:
                db.add_log(
                    "ERR-511",
                    f"Не удалось опубликовать отложенный пост {post['id']} "
                    f"(попытка {attempts} из {MAX_PUBLISH_ATTEMPTS}): {message}",
                )
    return published


def _mark_queue_entry_published(source: str, messages: list[Message]) -> None:
    """Помечает черновик в очереди как опубликованный.

    Пост мог остаться в очереди после сбоя. Когда он всё же ушёл, запись
    должна исчезнуть из списка ожидающих: иначе пользователь отправит
    его повторно и получит дубль в канале.
    """
    getter = getattr(db, "get_queued_post_by_source", None)
    updater = getattr(db, "update_queued_post", None)
    if not callable(getter) or not callable(updater):
        return
    for message in messages:
        try:
            row = getter(source, message.id)
        except Exception:
            return
        if not row:
            continue
        status = str(row.get("status") or "")
        if status in {"published", "cancelled"}:
            continue
        try:
            updater(
                row["id"],
                {
                    "status": "published",
                    "published_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "last_error": "",
                },
            )
        except Exception:
            continue


def _keep_post_for_manual_publish(
    source: str,
    messages: list[Message],
    text: str,
    target_chats: list[str | int],
    tags: list[str],
    reason: str,
) -> bool:
    """Кладёт пост в очередь, чтобы его можно было отправить вручную.

    Используется, когда автоматическая отправка не удалась: контент не
    должен пропадать. Запись в очереди пользователь увидит в разделе
    «Посты» и сможет отправить её вручную, когда проблема исчезнет.
    """
    try:
        queue_id = _save_post_to_queue(
            source, messages, text, list(target_chats), ", ".join(tags), [], []
        )
        if not queue_id:
            return False
        db.update_queued_post(queue_id, {"last_error": reason})
        db.add_log(
            "WARN-408",
            f"Пост {source}/{messages[0].id} сохранён в очереди для ручной отправки: {reason}",
        )
        print(
            f"--> Пост {source}/{messages[0].id} сохранён в очереди; "
            "отправьте его вручную в разделе «Посты»."
        )
        return True
    except Exception as exc:
        db.add_log(
            "ERR-510",
            f"Не удалось сохранить пост {source}/{messages[0].id} в очередь: {exc}",
        )
        print(f"!!! Не удалось сохранить пост {source}/{messages[0].id} в очередь: {exc}")
        return False


def _post_publish_attempts(post: dict) -> int:
    """Сколько раз уже пытались опубликовать пост."""
    try:
        return max(0, int(post.get("publish_attempts") or 0))
    except (TypeError, ValueError):
        return 0


async def _queue_source_draft_after_ai_failure(
    source: str,
    messages: list[Message],
    raw_text: str,
    cfg: dict,
    reason: str,
) -> bool:
    """Сохраняет пост в очередь, когда отправить его сейчас не получилось.

    Черновик нужен в любом режиме: пока ИИ недоступен или Telegram
    отклоняет отправку, пост не должен пропадать. В очереди он ждёт, и
    пользователь может отправить его вручную из панели, когда всё
    наладится.
    """
    routes = _effective_tag_routes(cfg)
    # Канал для черновика: запасной маршрут (тег «общее»). Без него
    # пользователь укажет канал вручную в панели.
    target_chats: list[str | int] = []
    for chat in routing.fallback_channels(routes):
        try:
            resolved = _target_chat_id(chat)
        except Exception:
            continue
        if resolved:
            target_chats.append(resolved)

    media_list: list = []
    temp_paths: list[str] = []
    media_error = ""
    try:
        media_list, temp_paths = await _download_media(messages, raw_text)
    except Exception as exc:
        # Черновик с текстом полезен и без вложения, если медиа не удалось скачать.
        # Причина ошибки сохраняется в записи очереди.
        media_error = f"Медиа не сохранено: {type(exc).__name__}: {exc}"
        media_list, temp_paths = [], []

    try:
        queue_id = _save_post_to_queue(
            source, messages, raw_text, target_chats, "общее", media_list, temp_paths
        )
        if not queue_id:
            raise RuntimeError("database returned an empty queue ID")

        detail = f"AI-рерайт не выполнен: {reason}. Сохранён исходный текст."
        if media_error:
            detail += f" {media_error}"
        db.update_queued_post(queue_id, {"last_error": detail})
        db.add_log(
            "WARN-406",
            f"Пост {source}/{messages[0].id} добавлен в очередь как черновик: {detail}",
        )
        print(
            f"--> Пост {source}/{messages[0].id} сохранён в очереди как черновик "
            "с исходным текстом; AI-рерайт можно повторить позже."
        )
        return True
    except Exception as exc:
        db.add_log(
            "ERR-510",
            f"Не удалось сохранить черновик {source}/{messages[0].id} в очередь: {exc}",
        )
        print(f"!!! Не удалось сохранить черновик {source}/{messages[0].id} в очередь: {exc}")
        return False
    finally:
        for path in temp_paths:
            with contextlib.suppress(OSError):
                os.remove(path)


async def _send_post_to_channel(
    bot: Bot,
    target_chat: str | int,
    media_list: list,
    final_text: str,
) -> tuple[bool, Exception | None]:
    """Отправляет пост в один канал.

    Возвращает (успех, ошибка). Ошибка при успешной отправке медиа означает,
    что не доставился только хвост текста — медиа в канале уже есть.
    """
    try:
        if media_list:
            # Подпись у картинки ограничена 1024 символами, а обычное
            # сообщение вмещает 4096. Если текст не влезает в подпись, но
            # влезает в сообщение, отправляем картинку без подписи, а текст
            # одним сообщением — иначе текст рвётся на две части.
            text_fits_caption = len(final_text) <= TG_CAPTION_LIMIT
            media_caption = final_text if text_fits_caption else None
            if len(media_list) == 1:
                single = media_list[0]
                if isinstance(single, InputMediaPhoto):
                    await _send_with_retry(
                        bot,
                        "send_photo",
                        chat_id=target_chat,
                        photo=single.media,
                        caption=media_caption or None,
                    )
                elif isinstance(single, InputMediaVideo):
                    await _send_with_retry(
                        bot,
                        "send_video",
                        chat_id=target_chat,
                        video=single.media,
                        caption=media_caption or None,
                    )
                else:
                    await _send_with_retry(
                        bot,
                        "send_document",
                        chat_id=target_chat,
                        document=single.media,
                        caption=media_caption or None,
                    )
            else:
                safe_media_group = _prepare_media_group(media_list)
                if not text_fits_caption:
                    for item in safe_media_group:
                        item.caption = None
                await _send_with_retry(
                    bot,
                    "send_media_group",
                    chat_id=target_chat,
                    media=safe_media_group,
                )

            # Текст, который не влез в подпись, уходит отдельным
            # сообщением. Он не делится надвое: подпись в подписи не
            # обрезается, хвост не отбрасывается.
            if final_text and not text_fits_caption:
                try:
                    await _send_with_retry(
                        bot,
                        "send_message",
                        chat_id=target_chat,
                        text=final_text,
                    )
                except Exception as exc:
                    return True, exc
        else:
            await _send_with_retry(
                bot, "send_message", chat_id=target_chat, text=final_text
            )
    except Exception as exc:
        return False, exc
    return True, None


async def _process_message_group(
    client: TelegramClient,
    source: str,
    messages: list[Message],
    cfg: dict,
    bot: Bot,
    ai_session: aiohttp.ClientSession,
) -> None:
    if not bot_running.is_set():
        return

    messages.sort(key=lambda m: m.id)
    captions = [m.message for m in messages if m.message]
    raw_text = "\n".join(captions).strip()

    has_media = any(_is_downloadable_media(m.media) for m in messages)
    if not raw_text and not has_media:
        has_unsupported_media = any(
            m.media is not None
            and not _is_media_skippable(m.media)
            and not _is_downloadable_media(m.media)
            for m in messages
        )
        reason = "unsupported_media" if has_unsupported_media else "empty"
        _save_skipped_group(source, messages, reason)
        return

    if raw_text and cfg["ads_words"] and _contains_ad(raw_text, cfg["ads_words"]):
        _save_skipped_group(source, messages, "ads_word")
        return

    if not bot_running.is_set():
        return

    print(f"+++ Обработка поста из {source} (ID={messages[0].id})...")
    post_tags: list[str]
    if raw_text and not _significant_words(raw_text):
        # Подпись без единого значимого слова — обычно одни эмодзи или тег.
        # Переписывать нечего, а ИИ на таком материале выдумывает новость.
        # Публикуем исходный текст: он точно соответствует картинке.
        post_tags, final_text = [], raw_text
    elif raw_text:
        try:
            post_tags, final_text = await _ai_rewrite(raw_text, cfg, ai_session)
        except AIResponseFormatError as exc:
            db.add_log(
                "WARN-404",
                f"Пост {source}/{messages[0].id} не опубликован: AI вернул неверный формат ({exc}). Оставлен для повтора.",
            )
            queued = await _queue_source_draft_after_ai_failure(
                source, messages, raw_text, cfg, str(exc)
            )
            if not queued:
                print(f"!!! AI вернул неверный формат для {source}/{messages[0].id}; пост оставлен для повтора.")
            return
        except AIRewriteError as exc:
            db.add_log(
                "WARN-405",
                f"Пост {source}/{messages[0].id} не опубликован: AI недоступен ({exc}). Повторим на следующем проходе.",
            )
            queued = await _queue_source_draft_after_ai_failure(
                source, messages, raw_text, cfg, str(exc)
            )
            if not queued:
                print(f"!!! AI-рерайт не удался для {source}/{messages[0].id}; пост оставлен для повтора.")
            return
    else:
        # Если в исходном посте нет текста, рерайт не требуется.
        post_tags, final_text = [], ""

    if isinstance(post_tags, str):
        # Старые адаптеры отдают тему строкой, а не списком тегов.
        post_tags = routing.parse_tag_list(post_tags)

    if raw_text and not _rewrite_is_about_original(raw_text, final_text):
        # Если пересказ не связан с исходным постом, используем оригинал,
        # чтобы текст публикации не противоречил исходному содержимому.
        db.add_log(
            "WARN-410",
            f"Пост {source}/{messages[0].id}: пересказ не связан с исходным "
            "текстом, опубликован исходный текст поста.",
        )
        print(f"!!! Пересказ не связан с оригиналом для {source}/{messages[0].id}; публикуем исходный текст.")
        post_tags, final_text = [], raw_text

    if cfg["watermark"]:
        final_text = f"{final_text}\n\n{cfg['watermark']}"
    # Приводим Markdown к HTML Telegram, чтобы форматирование было единообразным
    # независимо от того, какую разметку вернула модель.
    final_text = _sanitize_telegram_html(
        text_format.markdown_to_telegram_html(final_text)
    )


    # Каналы определяются тегами: пост уходит во все каналы, у которых
    # есть хотя бы один из его тегов.
    routes = _effective_tag_routes(cfg)
    matched_chats = routing.resolve_channels(routes, post_tags)
    target_chats = [_target_chat_id(chat) for chat in matched_chats if chat]
    target_chats = [chat for chat in target_chats if chat]
    if not target_chats:
        # Нет маршрута — это проблема настройки, а не повод выбрасывать
        # пост. Он остаётся в ожидании: добавить маршрут и повторить.
        db.add_log(
            "WARN-400",
            f"Для тегов [{', '.join(post_tags) or 'без тегов'}] не настроен целевой канал; "
            f"пост {source}/{messages[0].id} оставлен для повтора. Настройте маршрут "
            "с этими тегами или добавьте маршрут 'общее'.",
        )
        print(
            f"!!! Целевой канал для тегов [{', '.join(post_tags) or 'без тегов'}] "
            "не настроен; пост оставлен для повтора."
        )
        return

    if not bot_running.is_set():
        return

    try:
        media_list, temp_paths = await _download_media(messages, final_text)
    except FloodWaitError:
        # Передаём FloodWait основному циклу, чтобы он выдержал паузу Telegram
        # вместо повторения загрузки при каждом опросе.
        raise
    except Exception as exc:
        db.add_log(
            "WARN-501",
            f"Пост {source}/{messages[0].id} не опубликован: {exc}. Оставлен для повтора.",
        )
        print(f"!!! Не удалось скачать вложение для {source}/{messages[0].id}; пост оставлен для повтора.")
        return

    MAX_TG_LEN = TG_MESSAGE_LIMIT
    if len(final_text) > MAX_TG_LEN:
        original_len = len(final_text)
        final_text = _truncate_telegram_text(final_text, MAX_TG_LEN)
        db.add_log(
            "WARN-500",
            f"Текст ответа ИИ обрезан с {original_len} до {len(final_text)} символов для Telegram.",
        )


    # Ручной режим: пост не публикуется сразу, а попадает в очередь,
    # где его можно поправить, отложить, отправить или отменить.
    if (cfg.get("publish_mode") or "auto") == "manual":
        try:
            _save_post_to_queue(
                source, messages, final_text, target_chats,
                ", ".join(post_tags), media_list, temp_paths,
            )
        except Exception as exc:
            db.add_log(
                "ERR-510",
                f"Пост {source}/{messages[0].id} не попал в очередь: {exc}.",
            )
            print(f"!!! Не удалось сохранить пост {source}/{messages[0].id} в очередь: {exc}")
            return
        finally:
            # Копии медиа уже лежат в очереди — временные файлы больше не
            # нужны. Без этой очистки temp_media рос бы бесконечно.
            for temp_path in temp_paths:
                with contextlib.suppress(OSError):
                    os.remove(temp_path)
        db.add_log(
            "OK-510",
            f"Пост {source}/{messages[0].id} добавлен в очередь и ждёт подтверждения.",
        )
        print(
            f"Пост {source}/{messages[0].id} -> очередь "
            f"(цель: {', '.join(str(chat) for chat in target_chats)})."
        )
        return

    retry_delay_s = 0
    secondary_send_error: Exception | None = None
    secondary_text_to_send = ""
    published_chats: list[str | int] = []
    try:
        for target_chat in target_chats:
            sent, send_error = await _send_post_to_channel(
                bot, target_chat, media_list, final_text
            )
            if sent:
                published_chats.append(target_chat)
                if send_error is not None and secondary_send_error is None:
                    # Медиа доставлены, не доставился только хвост текста.
                    secondary_send_error = send_error
                    secondary_text_to_send = final_text
                continue

            assert send_error is not None
            _log_send_error(send_error, target_chat)
            retryable = isinstance(
                send_error,
                (
                    asyncio.TimeoutError,
                    aiohttp.ClientError,
                    TelegramNetworkError,
                    TelegramServerError,
                    TelegramRetryAfter,
                ),
            )
            if retryable:
                retry_delay_s = max(
                    retry_delay_s, int(getattr(send_error, "retry_after", 0))
                )
                db.add_log(
                    "WARN-406",
                    f"Пост {source}/{messages[0].id} не отправлен в {target_chat} из-за "
                    f"временной ошибки ({type(send_error).__name__}); оставлен для повторной попытки.",
                )
            elif _is_post_level_send_error(send_error):
                # Дефект конкретного поста (например, файл больше 50 МБ).
                # Останавливать из-за него весь парсер нельзя: лента должна
                # продолжаться. Пост помечается пропущенным и не повторяется.
                _save_skipped_group(source, messages, "send_failed")
                db.add_log(
                    "WARN-407",
                    f"Пост {source}/{messages[0].id} не доставлен в {target_chat}: Telegram "
                    f"отклонил медиа ({type(send_error).__name__}: {send_error}). "
                    "Пост пропущен, парсер продолжает работу.",
                )
                print(
                    f"!!! [WARN-407] Пост {source}/{messages[0].id} отклонён Telegram "
                    f"({send_error}); пропущен, парсер работает дальше."
                )
            else:
                # Отказ отправки может быть исправлен: причиной бывают права, канал, токен
                # или формат данных. Не пропускаем такой пост навсегда — останавливаем
                # цикл и оставляем его для повторной отправки.
                bot_running.clear()
                # Пост не должен пропасть: сохраняем его в очередь, чтобы
                # после исправления отправить вручную из панели.
                _keep_post_for_manual_publish(
                    source, messages, final_text, target_chats, post_tags,
                    f"Telegram отклонил отправку: {type(send_error).__name__}: {send_error}",
                )
                db.add_log(
                    "ERR-408",
                    f"Пост {source}/{messages[0].id} не доставлен в {target_chat} из-за "
                    f"постоянной ошибки ({type(send_error).__name__}); пост сохранён в очереди "
                    "для ручной отправки, парсер остановлен. Исправьте токен/права/целевой "
                    "канал и запустите парсер снова.",
                )
                print(
                    f"!!! [ERR-408] Отправка поста {source}/{messages[0].id} отклонена; "
                    "пост сохранён для повтора, парсер остановлен."
                )
                break

        if published_chats:
            failed_post_id = messages[0].id
            followup_queued = False
            try:
                if secondary_send_error is not None:
                    # Медиа уже опубликовано. Одной транзакцией сохраняем отметки дедупликации
                    # и недоставленный остаток текста, чтобы не отправлять медиа повторно.
                    save_with_followup = getattr(db, "save_posts_with_followup", None)
                    if not callable(save_with_followup):
                        raise DatabaseWriteError(
                            "database adapter cannot atomically persist a failed text follow-up"
                        )
                    save_with_followup(
                        source,
                        [message.id for message in messages],
                        published_chats[0],
                        secondary_text_to_send,
                    )
                    followup_queued = True
                else:
                    # Основная реализация базы атомарно фиксирует идентификаторы альбома.
                    # Запасной путь поддерживает упрощённые адаптеры.
                    save_group = getattr(db, "save_posts", None)
                    if callable(save_group):
                        save_group(source, [message.id for message in messages])
                    else:
                        for m in messages:
                            failed_post_id = m.id
                            db.save_post(source, m.id)
                # Пост опубликован — черновик в очереди (если он был) больше
                # не ждёт отправки, иначе пользователь отправит его дважды.
                _mark_queue_entry_published(source, messages)
            except DatabaseWriteError as db_error:
                # Telegram уже принял сообщение. Останавливаем парсер, чтобы не отправлять
                # следующие посты без сохранённых записей дедупликации.
                bot_running.clear()
                db.add_log(
                    "ERR-DB-501",
                    f"Пост {source}/{failed_post_id} уже отправлен в Telegram, "
                    f"но запись в SQLite не удалась ({db_error}); бот остановлен. "
                    "Перед перезапуском проверьте целевой канал.",
                )
                print(
                    f"!!! Публикация {source}/{failed_post_id} уже отправлена, "
                    "но не записана в SQLite. Бот остановлен; проверьте канал перед запуском."
                )
            else:
                if followup_queued:
                    db.add_log(
                        "WARN-407",
                        f"Основная публикация {source}/{messages[0].id} уже отправлена в "
                        f"{published_chats[0]}, но остаток текста не доставлен "
                        f"({type(secondary_send_error).__name__}). Остаток атомарно сохранён "
                        "в SQLite и будет повторно отправлен без дублирования медиа.",
                    )
                db.add_log(
                    "OK-501",
                    f"Пост {source}/{messages[0].id} успешно опубликован в "
                    f"{', '.join(str(chat) for chat in published_chats)} "
                    f"(теги: {', '.join(post_tags) or 'без тегов'}).",
                )
                print(
                f"УСПЕХ: → [{', '.join(post_tags) or 'общее'}] -> "
                f"{', '.join(str(chat) for chat in published_chats)}"
            )
    finally:
        for p in temp_paths:
            with contextlib.suppress(OSError):
                os.remove(p)
    await asyncio.sleep(max(SLEEP_BETWEEN_POSTS_S, retry_delay_s))


async def _convert_media_for_telegram(path: str) -> str:
    """Приводит видеофайл к MP4, если Telegram не умеет проигрывать исходный.

    Telegram принимает в sendVideo только MP4: MOV уезжает в чат файлом, а
    GIF превращается в статичную картинку. Конвертация выполняется через
    ffmpeg. Если ffmpeg нет или он не справился, возвращается исходный
    файл — он уйдёт в чат как документ, публикация при этом не ломается.
    """
    if not media_convert.needs_conversion(path):
        return path
    converted = await asyncio.to_thread(media_convert.convert_to_mp4, path)
    if not converted:
        db.add_log(
            "WARN-409",
            f"Не удалось перевести видео в MP4 ({os.path.basename(path)}): "
            "файл будет отправлен как документ. Установите ffmpeg, чтобы видео "
            "проигрывалось в канале.",
        )
        print(f"!!! [WARN-409] Не удалось конвертировать {os.path.basename(path)} в MP4")
        return path
    with contextlib.suppress(OSError):
        os.remove(path)
    print(f"--> [МЕДИА] {os.path.basename(path)} переведён в MP4")
    return converted


async def _download_media(messages: list[Message], caption: str = "") -> tuple[list, list[str]]:
    media_list = []
    temp_paths = []
    cap_text = (caption or "").strip()
    if cap_text and len(cap_text) > 1024:
        cap_text = _media_caption_prefix(cap_text) + "…"
    caption_assigned = False
    try:
        for m in messages:
            if _is_media_skippable(m.media) or not _is_downloadable_media(m.media):
                continue
            path = await m.download_media(file=f"{TEMP_DIR}/", progress_callback=_progress_bar)
            if not path:
                if _is_downloadable_media(m.media):
                    raise MediaDownloadError(
                        f"Telethon не вернул файл для сообщения {getattr(m, 'id', '?')}"
                    )
                continue
            path = await _convert_media_for_telegram(path)
            temp_paths.append(path)
            m_t = _classify_extension(path)
            cap = cap_text if cap_text and not caption_assigned else None
            if cap is not None:
                caption_assigned = True
            media_list.append(
                m_t(
                    media=FSInputFile(path, filename=media_marker.clean_filename(path)),
                    caption=cap,
                )
            )

    except BaseException:
        # Вызывающий код не сможет убрать эти файлы, если функция не вернулась.
        for path in temp_paths:
            with contextlib.suppress(OSError):
                os.remove(path)
        raise
    print()
    return media_list, temp_paths


#: Признаки ошибок, относящихся к конкретному посту, а не к настройкам
#: бота: слишком большой файл, неподдерживаемый формат, битая ссылка.
_POST_LEVEL_BAD_REQUEST_MARKERS = (
    "request entity too large",
    "too big",
    "file is too large",
    "message is too long",
    "caption is too long",
    "wrong file identifier",
    "file_reference_expired",
    "photo_invalid_dimensions",
    "video_content_type",
    "unsupported",
    "wrong file",
)


def _is_post_level_send_error(exc: Exception) -> bool:
    """Ошибка относится к конкретному посту, а не к настройкам бота.

    Слишком большой файл или неподдерживаемый формат — это дефект одного
    поста. Останавливать из-за него весь парсер нельзя: остальные посты
    публикуются нормально, а пользователь увидит только обрыв ленты.
    """
    if isinstance(exc, TelegramEntityTooLarge):
        return True
    if not isinstance(exc, TelegramBadRequest):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _POST_LEVEL_BAD_REQUEST_MARKERS)


def _log_send_error(send_error: Exception, target_chat) -> None:
    msg = str(send_error).lower()
    if "unauthorized" in msg or "token" in msg:
        db.add_log("ERR-202", f"Неверный токен бота-отправщика: {send_error}")
        print("!!! [ERR-202] Токен бота недействителен.")
    elif "chat not found" in msg or "admin" in msg:
        db.add_log(
            "ERR-205",
            f"Бот не добавлен в {target_chat} или не имеет прав публикации: {send_error}",
        )
        print(f"!!! Добавьте бота в {target_chat} как администратора.")
    elif "proxy" in msg or "connect" in msg:
        db.add_log("ERR-301", f"Прокси бота не отвечает: {send_error}")
        print("!!! [ERR-301] Прокси недоступен.")
    else:
        db.add_log("ERR-200", f"Сбой отправки: {send_error}")
        print(f"!!! ОШИБКА ОТПРАВКИ: {send_error}")


class AIRewriteError(RuntimeError):
    """AI не смог подготовить текст, безопасный для публикации."""


class AIResponseFormatError(AIRewriteError):
    """Successful AI response does not contain publishable structured content."""


def _effective_tag_routes(cfg: dict) -> dict[str, list[str]]:
    """Маршруты по тегам для текущей конфигурации.

    Если маршруты по тегам не заданы, используются старые настройки
    «категория → канал»: категория становится тегом своего канала.
    """
    routes = cfg.get("tag_routes") or {}
    if routes:
        return routes
    return routing.parse_routes(cfg.get("topic_channels") or {})


async def _ai_rewrite(raw_text: str, cfg: dict, session_ai: aiohttp.ClientSession) -> tuple[list[str], str]:
    routes = _effective_tag_routes(cfg)
    tags = ", ".join(cfg.get("tag_routes_tags") or routing.all_tags(routes)) or routing.FALLBACK_TAG
    db_prompt = (db.get_setting("system_prompt", "") or "").strip()
    # За основу берём промпт пользователя, а если его нет — рекомендуемый.
    base_prompt = db_prompt or DEFAULT_SYSTEM_PROMPT
    # Правила разметки Telegram добавляем всегда: без них пересказ приходит
    # голым текстом, без цитат, ссылок и акцентов.
    if "<blockquote>" not in base_prompt:
        base_prompt = f"{base_prompt}\n\n{TELEGRAM_FORMATTING_RULES}"
    s_prompt = (
        f"{base_prompt}\n\n"
        f"Доступные теги для маршрутизации: [{tags}]\n"
        "В ответе обязательно должен быть раздел с тегами публикации — "
        "ТЕГИ, ХЭШТЕГИ или ТЕМА, как требует твой формат ответа. "
        "Теги определяют, в какие каналы уйдёт пост: он попадёт во все каналы, "
        "у которых есть такой тег. Если ни один тег не подошёл, пост уйдёт в канал "
        "с тегом «общее»."
    )

    if cfg["ai_mode"] == "LOCAL":
        url, model = cfg["local_url"], cfg["local_model"]
        headers = {}
    else:
        url, model = cfg["remote_url"], cfg["remote_model"]
        headers = {"Authorization": f"Bearer {cfg['remote_api_key']}"} if cfg.get("remote_api_key") else {}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": s_prompt},
            {"role": "user", "content": raw_text},
        ],
        "temperature": AI_TEMPERATURE,
    }

    failure_reason = "неизвестная ошибка AI"
    request_timeout = aiohttp.ClientTimeout(
        total=cfg.get("ai_timeout") or AI_REQUEST_TIMEOUT_S
    )
    try:
        async with session_ai.post(url, json=payload, headers=headers,
                                   timeout=request_timeout) as r:
            if r.status == 200:
                data = await r.json()
                res = _strip_reasoning_blocks(_extract_ai_content(data))
                if not res:
                    failure_reason = "AI вернул пустой ответ"
                    db.add_log("ERR-400", failure_reason)
                    print("!!! [AI ERROR] Пустой ответ от AI endpoint")
                else:
                    ai_tags, rewritten_text = _parse_ai_response(res, raw_text)
                    if rewritten_text.strip():
                        return ai_tags, rewritten_text
                    failure_reason = "AI вернул пустой текст после разбора"
                    db.add_log("ERR-400", failure_reason)
            else:
                body = await r.text()
                _log_ai_http_error(r.status, cfg, body)
                failure_reason = f"AI endpoint вернул HTTP {r.status}"
    except AIResponseFormatError as e:
        db.add_log("ERR-400", f"Ответ AI не соответствует формату публикации: {e}")
        print(f"!!! [AI ERROR] Некорректный формат ответа: {e}")
        raise
    except aiohttp.ClientProxyConnectionError as e:
        # Отделяем ошибку подключения к прокси от ошибки AI-провайдера:
        # причина может быть в параметрах или доступности прокси.
        _log_ai_network_error(e, cfg)
        failure_reason = "Не удалось подключиться к AI через прокси"
    except aiohttp.ClientConnectorError as e:
        _log_ai_network_error(e, cfg)
        failure_reason = "AI endpoint недоступен"
    except aiohttp.ClientPayloadError as e:
        # Сервер ответил, но прислал не тот JSON: например, это прокси,
        # который вместо ответа вернул свою страницу с ошибкой.
        db.add_log("ERR-404", f"AI вернул ответ, который не разбирается: {e}")
        failure_reason = "AI вернул некорректный ответ (не JSON)"
    except aiohttp.ServerDisconnectedError as e:
        _log_ai_network_error(e, cfg)
        failure_reason = "AI закрыл соединение"
    except aiohttp.ClientError as e:
        _log_ai_network_error(e, cfg)
        failure_reason = f"Ошибка связи с AI: {type(e).__name__}"
    except TimeoutError:

        timeout_value = cfg.get("ai_timeout") or AI_REQUEST_TIMEOUT_S

        failure_reason = (
            f"модель не ответила за {timeout_value} сек (TimeoutError) — "
            "увеличьте «Таймаут ИИ» в панели"
        )

        db.add_log("ERR-400", failure_reason)

        print(f"!!! [AI ERROR] {failure_reason}")

    except Exception as e:
        db.add_log("ERR-400", f"Неизвестный сбой AI: {e}")
        print(f"!!! [AI ERROR] {e}")
        failure_reason = f"ошибка ответа AI ({type(e).__name__})"

    # Не публикуем обрезок оригинала: вызывающий код оставит запись для повтора.
    raise AIRewriteError(failure_reason)


def _ai_content_to_text(candidate: object) -> str:
    """Приводит содержимое ответа модели к обычному тексту."""
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, list):
        parts: list[str] = []
        for item in candidate:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                chunk = item.get("text") or item.get("content") or ""
                if isinstance(chunk, str):
                    parts.append(chunk)
                elif isinstance(chunk, list):
                    parts.append(_ai_content_to_text(chunk))
        return "".join(part for part in parts if part)
    if isinstance(candidate, dict):
        chunk = candidate.get("text") or candidate.get("content") or ""
        return _ai_content_to_text(chunk)
    return ""


def _extract_ai_content(data: object) -> str:
    """Достаёт текст ответа ИИ из разных форматов API.

    Поддерживаются OpenAI-совместимый (``choices``), Ollama/LM Studio
    (``message``, ``response``), Anthropic (``content``) и Responses API
    (``output_text``, ``output``).
    """
    if not isinstance(data, dict):
        return ""

    choices = data.get("choices")
    if isinstance(choices, list):
        for item in choices:
            if not isinstance(item, dict):
                continue
            message = item.get("message")
            if isinstance(message, dict):
                for key in ("content", "text", "output_text"):
                    text_value = _ai_content_to_text(message.get(key))
                    if text_value.strip():
                        return text_value
            for key in ("text", "content", "output_text"):
                text_value = _ai_content_to_text(item.get(key))
                if text_value.strip():
                    return text_value

    for key in ("output_text", "response", "text", "content"):
        text_value = _ai_content_to_text(data.get(key))
        if text_value.strip():
            return text_value

    message = data.get("message")
    if isinstance(message, dict):
        for key in ("content", "text", "output_text"):
            text_value = _ai_content_to_text(message.get(key))
            if text_value.strip():
                return text_value

    output = data.get("output")
    if isinstance(output, list):
        for item in output:
            text_value = _ai_content_to_text(item)
            if text_value.strip():
                return text_value

    return ""


def _strip_reasoning_blocks(text: str) -> str:
    """Убирает служебные блоки размышления моделей (<think>...</think>).

    Модели часто не закрывают тег<think> — размышление уходит в пост
    целиком. Незакрытый блок вырезается до конца ответа.
    """
    if not text:
        return ""
    cleaned = re.sub(
        r"<(think|thinking|reasoning)>.*?</\1>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Незакрытый блок: вырезаем от тега до конца ответа.
    cleaned = re.sub(
        r"<(?:think|thinking|reasoning)>.*\Z",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Вариант служебных маркеров без угловых скобок: <|thinking|>
    cleaned = re.sub(
        r"<\|(?:thinking|thought|reasoning)\|>.*?(?:<\|(?:thinking|thought|reasoning)\|>|\Z)",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return cleaned.strip()


#: Метки разделов в ответе модели. Локальные модели часто оборачивают их
#: в Markdown-выделение: «**ТЕМА:** политика». Метка должна распознаваться
#: в любом оформлении, иначе тема не находится и пост уходит не туда.
_AI_SECTIONS = ("ТЕМА", "ТЕКСТ", "ХЭШТЕГИ")

#: Метка, обёрнутая в выделение: **ТЕМА:**, **ТЕМА**, *ТЕМА:*, __ТЕМА__:.
#: Между обвязкой и меткой допускаются только горизонтальные пробелы —
#: иначе выделение текста рядом с меткой принято за метку.
_AI_LABEL_RE = re.compile(
    r"(?P<mark>\*\*|__|\*|_|`)[ \t]*"
    r"(?P<label>ТЕМА|ТЕКСТ|ХЭШТЕГИ|ТЕГИ)[ \t]*"
    r"(?::)?[ \t]*(?P=mark)(?::)?",
    re.IGNORECASE,
)


def _normalize_ai_labels(res: str) -> str:
    """Приводит метки разделов к виду «ТЕМА:», снимая Markdown-обвязку."""
    return _AI_LABEL_RE.sub(lambda m: f"{m.group('label')}:", res)


def _strip_emphasis(value: str) -> str:
    """Снимает внешнее Markdown-выделение с короткого значения."""
    return value.strip().strip("*_`").strip()


def _extract_ai_topic(normalized: str) -> str:
    """Достаёт тему из ответа модели.

    Значение может стоять на следующей строке — тогда берём первую
    непустую строку, которая не является следующей меткой.
    """
    match = re.search(r"ТЕМА[ \t]*:[ \t]*([^\n]*)", normalized, re.IGNORECASE)
    if not match:
        return "общее"

    topic = _strip_emphasis(match.group(1))
    if topic:
        return topic.lower()

    for line in normalized[match.end():].splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if re.match(r"^(?:ТЕКСТ|ХЭШТЕГИ)[ \t]*:", candidate, re.IGNORECASE):
            break
        return _strip_emphasis(candidate).lower()
    return "общее"


def _parse_ai_response(res: str, raw_text: str) -> tuple[list[str], str]:
    """Разбирает ответ модели: теги для маршрутизации и текст поста.

    Теги нужны только для выбора канала — в самом посте их быть не
    должно, поэтому они вырезаются из текста.
    """
    if not isinstance(res, str) or not res.strip():
        raise AIResponseFormatError("пустой ответ")

    normalized = _normalize_ai_labels(res)
    c_m = re.search(r"ТЕКСТ[ \t]*:[ \t]*(.*)", normalized, re.DOTALL | re.IGNORECASE)
    if not c_m:
        raise AIResponseFormatError("отсутствует обязательный раздел ТЕКСТ:")

    full = c_m.group(1).strip()
    # Секция тегов обрывает текст: дальше идут только теги.
    clean = re.split(
        r"(?:ХЭШТЕГИ|ТЕГИ)[ \t]*:", full, maxsplit=1, flags=re.IGNORECASE
    )[0].strip()
    if not clean:
        raise AIResponseFormatError("раздел ТЕКСТ: пуст")

    ai_tags = _collect_ai_tags(normalized)

    # Хештеги, оставшиеся в самом тексте, тоже определяют маршрут —
    # и тоже удаляются из поста.
    inline_tags = routing.extract_tags(clean)
    for tag in inline_tags:
        if tag not in ai_tags:
            ai_tags.append(tag)

    return ai_tags, routing.strip_hashtags(clean)


def _collect_ai_tags(normalized: str) -> list[str]:
    """Собирает теги из разделов ТЕГИ, ХЭШТЕГИ и ТЕМА.

    Берутся все найденные разделы, а не только первый: старый промпт
    отдаёт категорию в «ТЕМА:» и хештеги в «ХЭШТЕГИ:», новый — теги в
    «ТЕГИ:». Любой из них подходит для маршрутизации.
    """
    tags: list[str] = []

    def _add(values: list[str]) -> None:
        for tag in values:
            if tag and tag not in tags:
                tags.append(tag)

    for pattern in (
        r"ТЕГИ[ \t]*:[ \t]*(.*)",
        r"ХЭШТЕГИ[ \t]*:[ \t]*(.*)",
        r"ТЕМА[ \t]*:[ \t]*([^\n]*)",
    ):
        match = re.search(pattern, normalized, re.DOTALL | re.IGNORECASE)
        if not match:
            continue
        _add(routing.parse_tag_list(match.group(1)))
    return tags


def _log_ai_http_error(status: int, cfg: dict, body: str) -> None:
    if cfg["ai_mode"] == "LOCAL":
        db.add_log("ERR-402", f"LM Studio вернул {status}. Модель не загружена? Ответ: {body[:100]}")
        print(f"!!! [ERR-402] LM Studio: код {status}")
    else:
        db.add_log("ERR-403", f"Remote API вернул {status}. Ответ: {body[:100]}")
        print(f"!!! [ERR-403] Облако: код {status}")


def _log_ai_network_error(err: Exception, cfg: dict) -> None:
    if cfg["ai_mode"] == "LOCAL":
        db.add_log("ERR-401", f"LM Studio недоступен: {err}")
        print("!!! [ERR-401] Не удалось связаться с LM Studio")
    else:
        db.add_log("ERR-403", f"Сеть к облаку: {err}")
        print("!!! [ERR-403] Нет связи с сервером")


# ==========================================
# ГЛАВНЫЙ ЦИКЛ
# ==========================================
async def _wait_for_resume() -> None:
    print("--> [СИСТЕМА] Бот на паузе. Нажмите 'Запустить парсер' в админ-панели.")
    while not bot_running.is_set():
        await asyncio.sleep(POLL_PAUSE_S)
    print("--> [СИСТЕМА] Сигнал получен! Начинаю парсинг...")


async def _wait_for_settings() -> None:
    print("--- [ОЖИДАНИЕ] Настройки Telegram API или bot_token пусты. Заполните их в панели.")
    await asyncio.sleep(SLEEP_NO_CONFIG_S)


async def _retry_pending_text_followups(bot: Bot, limit: int = 20) -> None:
    """Retry durable caption tails without re-publishing their already-sent media."""
    get_due = getattr(db, "get_due_pending_text_followups", None)
    if not callable(get_due):
        return

    try:
        pending = get_due(limit)
    except Exception as exc:
        bot_running.clear()
        with contextlib.suppress(Exception):
            db.add_log(
                "ERR-DB-502",
                f"Не удалось прочитать очередь остатков текста ({type(exc).__name__}: {exc}); парсер остановлен.",
            )
        return

    for item in pending:
        if not bot_running.is_set():
            return
        followup_id = int(item["id"])
        try:
            await _send_with_retry(
                bot,
                "send_message",
                chat_id=item["target_chat"],
                text=item["text"],
            )
        except Exception as send_error:
            retry_after = getattr(send_error, "retry_after", None)
            try:
                db.record_pending_text_followup_failure(
                    followup_id,
                    f"{type(send_error).__name__}: {send_error}",
                    retry_after_seconds=retry_after,
                )
            except Exception as db_error:
                bot_running.clear()
                with contextlib.suppress(Exception):
                    db.add_log(
                        "ERR-DB-503",
                        f"Не удалось запланировать повтор остатка текста #{followup_id} "
                        f"({db_error}); парсер остановлен.",
                    )
                return

            db.add_log(
                "WARN-407",
                f"Остаток текста для {item['source_name']}/{item['post_id']} "
                f"не доставлен ({type(send_error).__name__}); повтор отложен.",
            )
            if isinstance(send_error, TelegramRetryAfter):
                # Соблюдаем лимит Telegram и не обрабатываем новые сообщения очереди
                # в том же цикле.
                break
        else:
            try:
                db.mark_pending_text_followup_sent(followup_id)
            except Exception as db_error:
                bot_running.clear()
                with contextlib.suppress(Exception):
                    db.add_log(
                        "ERR-DB-504",
                        f"Telegram принял остаток текста #{followup_id}, но SQLite не смог "
                        f"удалить его из очереди ({db_error}). Парсер остановлен; "
                        "перед перезапуском проверьте канал, чтобы избежать возможного дубля.",
                    )
                return

            db.add_log(
                "OK-502",
                f"Остаток текста для {item['source_name']}/{item['post_id']} "
                "доставлен из очереди повторных отправок.",
            )


def _stdin_is_interactive() -> bool:
    """Проверяет, можно ли безопасно запросить у пользователя телефон или код входа."""
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _process_image_name(pid: int) -> str:
    """Полный путь к исполняемому файлу процесса (пустая строка при ошибке)."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


def _console_belongs_to_app() -> bool:
    """True, если консолью пользуется только наше приложение.

    У собранного .exe в консоли два процесса: загрузчик PyInstaller и сама
    программа, поэтому одного процесса недостаточно, чтобы судить о
    владельце консоли. Прячем окно только когда в консоли нет посторонних
    процессов — тогда её создали для нас (например, двойным кликом по
    файлу) и её никто другой не использует.
    """
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetConsoleProcessList.argtypes = [
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_ulong,
        ]
        kernel32.GetConsoleProcessList.restype = ctypes.c_ulong

        buffer = (ctypes.c_ulong * 16)()
        attached = kernel32.GetConsoleProcessList(buffer, 16)
        if attached <= 0:
            return False

        own_name = os.path.basename(sys.executable).lower()
        for index in range(attached):
            image = _process_image_name(buffer[index])
            if not image:
                return False
            if os.path.basename(image).lower() != own_name:
                return False
        return True
    except Exception:
        return False


def _hide_console_window() -> None:
    """Прячет окно консоли после успешного входа в Telegram.

    Консоль нужна только для запроса номера телефона и кода подтверждения.
    Когда вход пройден, окно скрывается, чтобы не висеть в панели задач.
    Само приложение продолжает работать, панель управления остаётся
    доступна по http://127.0.0.1:8000/.

    Прячется только собственная консоль приложения: если программа
    запущена из уже открытого окна терминала, чужое окно не трогаем.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.ShowWindow.restype = ctypes.c_int

        if not _console_belongs_to_app():
            return

        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        # Прятать окно — необязательный шаг, его неудача не должна мешать работе.
        pass


async def _connect_authorized_client(
    client: TelegramClient,
    *,
    allow_interactive: bool = False,
) -> bool:
    """По умолчанию подключается без интерактивного входа; запрос кода разрешён только в явном TTY-режиме."""
    if allow_interactive:
        await client.start()
    else:
        await client.connect()

    if await client.is_user_authorized():
        return True
    db.add_log(
        "ERR-203",
        "Telegram-сессия не авторизована. Запустите в интерактивном терминале, "
        "чтобы пройти вход по телефону/коду, или укажите готовый session-файл.",
    )
    print("!!! [ERR-203] Telegram-сессия не авторизована.")
    return False


async def _wait_for_db() -> None:
    print("!!! [БАЗА ДАННЫХ] Ошибка загрузки настроек. Повтор через 10 сек.")
    await asyncio.sleep(SLEEP_DB_ERROR_S)


_SOURCE_CURSORS_SETTING = "_internal_source_cursors_json"


class SourceCursorStateError(RuntimeError):
    """Stored polling cursors are corrupt; do not silently re-baseline history."""


def _load_source_cursors() -> dict[str, int]:
    raw = db.get_setting(_SOURCE_CURSORS_SETTING, None)
    if raw is None or not str(raw).strip():
        return {}
    try:
        cursors = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SourceCursorStateError("JSON курсоров повреждён") from exc
    if not isinstance(cursors, dict):
        raise SourceCursorStateError("JSON курсоров должен быть объектом")

    normalized: dict[str, int] = {}
    for source, value in cursors.items():
        if (
            not isinstance(source, str)
            or not source.strip()
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise SourceCursorStateError(
                f"Некорректная запись курсора для источника {source!r}"
            )

        source_key = source.strip()
        # Ранее курсор сохранялся по введённой ссылке (например, https://t.me/Channel/123).
        # Текущая конфигурация использует нормализованные имена и ID. Сохраняем
        # прежний курсор, чтобы обновление не запускало первичное сканирование.
        # Курсор удалённого или некорректного источника не должен блокировать
        # остальные каналы; он не совпадёт ни с одним действующим источником.
        with contextlib.suppress(ValueError):
            source_key = normalize_target_chat(source_key) or source_key

        previous = normalized.get(source_key)
        # При совпадении старых псевдонимов выбираем самый ранний курсор. Уже опубликованные
        # сообщения отсекаются по базе; поздний курсор мог бы пропустить ожидающие посты.
        normalized[source_key] = value if previous is None else min(previous, value)
    return normalized


def _get_source_cursor(source: str) -> int | None:
    return _load_source_cursors().get(source)


def _set_source_cursor(source: str, message_id: int) -> None:
    cursors = _load_source_cursors()
    cursors[source] = int(message_id)
    db.set_setting(
        _SOURCE_CURSORS_SETTING,
        json.dumps(cursors, ensure_ascii=False, sort_keys=True),
    )


async def _process_one_source(
    client: TelegramClient,
    source: str,
    cfg: dict,
    bot: Bot,
    ai_session: aiohttp.ClientSession,
) -> bool:
    if not bot_running.is_set():
        print("--> [СИСТЕМА] Парсинг прерван пользователем.")
        return False
    print(f"--> Проверка канала: {source}")
    try:
        cursor = _get_source_cursor(source)
    except SourceCursorStateError as exc:
        db.add_log(
            "ERR-207",
            f"Источник {source} оставлен без обработки: курсор повреждён ({exc}). "
            "Курсор не сброшен, чтобы не пропустить историю. Исправьте значение "
            f"{_SOURCE_CURSORS_SETTING} в базе настроек.",
        )
        print(f"!!! [ERR-207] Курсор {source} повреждён; источник пропущен без сброса.")
        return False

    source_messages: list[Message] = []
    try:
        if cursor is None:
            # При первом запуске начинаем с последних десяти сообщений, а не публикуем
            # всю историю канала.
            async for message in client.iter_messages(_source_entity(source), limit=10):
                source_messages.append(message)
            source_messages.sort(key=lambda message: message.id)

            # Первое из десяти сообщений может быть частью альбома. Получаем остальные
            # элементы группы до установки курсора, чтобы не пропустить её начало.
            if source_messages and source_messages[0].grouped_id:
                boundary_group = source_messages[0].grouped_id
                async for earlier in client.iter_messages(
                    _source_entity(source),
                    max_id=source_messages[0].id,
                    limit=100,
                ):
                    if earlier.grouped_id == boundary_group:
                        source_messages.append(earlier)
                source_messages.sort(key=lambda message: message.id)
        else:
            # При последующих опросах читаем все сообщения новее курсора, а не только десять последних.
            async for message in client.iter_messages(_source_entity(source), min_id=cursor, reverse=True):
                source_messages.append(message)
    except (AuthKeyUnregisteredError, SessionPasswordNeededError) as e:
        db.add_log("ERR-203", f"Сессия Telegram невалидна ({type(e).__name__}): {e}")
        print(f"!!! [ERR-203] Сессия Telegram закрыта / требует 2FA: {e}")
        # Повторное подключение не исправит отозванную сессию или требование 2FA.
        # Останавливаем парсер до восстановления действующей сессии.
        bot_running.clear()
        return False
    except (ChannelInvalidError, UsernameInvalidError, UsernameNotOccupiedError) as e:
        db.add_log("ERR-200", f"Канал не найден {source!r}: {e}")
        print(f"!!! Ошибка доступа к {source}: канал не найден.")
        return False
    except (ChannelPrivateError, ChatForbiddenError) as e:
        db.add_log("ERR-200", f"Нет доступа к каналу {source!r}: {e}")
        print(f"!!! Ошибка доступа к {source}: канал приватный или бот не подписан.")
        return False
    except FloodWaitError:
        # Передаём исключение в main(), где выдерживается указанная Telegram пауза.
        raise
    except Exception as e:
        db.add_log("ERR-200", f"Не удалось прочитать {source}: {e}")
        print(f"!!! Ошибка доступа к {source}: {e}")
        return False

    if not source_messages:
        return False

    source_messages.sort(key=lambda message: message.id)
    if cursor is None:
        cursor = max(0, source_messages[0].id - 1)
        _set_source_cursor(source, cursor)

    grouped: dict = {}
    for message in source_messages:
        gid = message.grouped_id or f"single_{message.id}"
        grouped.setdefault(gid, []).append(message)

    completed_groups: dict[object, bool] = {}
    cursor_index = 0

    def advance_cursor() -> None:
        nonlocal cursor, cursor_index
        previous_cursor = cursor if cursor is not None else 0
        while cursor_index < len(source_messages):
            message = source_messages[cursor_index]
            gid = message.grouped_id or f"single_{message.id}"
            if not completed_groups.get(gid, False):
                break
            cursor = message.id
            cursor_index += 1
        if cursor is not None and cursor > previous_cursor:
            _set_source_cursor(source, cursor)

    # Очередь работает только в ручном режиме: в автоматическом пост
    # публикуется сразу. Без этой проверки черновик, оставшийся от ручного
    # режима, навсегда блокировал бы автопубликацию своих постов.
    queue_marker_active = cfg.get("publish_mode") == "manual"

    def post_has_pending_queue_entry(message_id: int | str) -> bool:
        if not queue_marker_active:
            return False
        checker = getattr(db, "is_post_queued", None)
        return callable(checker) and checker(source, message_id)

    for gid, messages in grouped.items():
        if not bot_running.is_set():
            break

        if post_has_pending_queue_entry(messages[0].id):
            completed_groups[gid] = True
            advance_cursor()
            continue

        pending = [
            message for message in messages
            if not db.is_published(source, message.id)
            and not db.is_skipped(source, message.id)
        ]
        if pending:
            await _process_message_group(
                client, source, pending, cfg, bot, ai_session
            )

        queued_group = post_has_pending_queue_entry(messages[0].id)
        completed_groups[gid] = queued_group or all(
            db.is_published(source, message.id)
            or db.is_skipped(source, message.id)
            for message in messages
        )
        advance_cursor()
        if not completed_groups[gid]:
            # Пост не удалось обработать. Считаем попытки: без этого он
            # повторялся бы вечно и блокировал всю ленту источника — новые
            # посты до него не доходили.
            attempt_key = f"{source}:{messages[0].id}"
            attempts = _POISON_ATTEMPTS.get(attempt_key, 0) + 1
            _POISON_ATTEMPTS[attempt_key] = attempts
            if attempts >= MAX_POST_ATTEMPTS:
                _save_skipped_group(source, messages, "retries_exhausted")
                db.add_log(
                    "WARN-409",
                    f"Пост {source}/{messages[0].id} не обработан после {attempts} попыток; "
                    "помечен как пропущенный, чтобы не блокировать ленту источника.",
                )
                print(
                    f"!!! [WARN-409] Пост {source}/{messages[0].id} не обработан "
                    f"за {attempts} попыток; пропущен."
                )
                completed_groups[gid] = True
                advance_cursor()
                continue
            # Оставляем пост доступным для повтора и не двигаем курсор
            # дальше: новые посты пока не обрабатываем.
            return False
        _POISON_ATTEMPTS.pop(f"{source}:{messages[0].id}", None)
    return False



def _connection_signature(cfg: dict) -> tuple:
    """Возвращает параметры подключения, изменение которых требует пересоздания клиента."""
    return (
        str(cfg.get("bot_token") or ""),
        str(cfg.get("api_id") or ""),
        str(cfg.get("api_hash") or ""),
        bool(cfg.get("use_proxy")),
        str(cfg.get("proxy_type") or ""),
        str(cfg.get("proxy_host") or ""),
        str(cfg.get("proxy_port") or ""),
        str(cfg.get("proxy_user") or ""),
        str(cfg.get("proxy_pass") or ""),
        str(cfg.get("mtproto_key") or ""),
    )


def _client_is_connected(client) -> bool:
    """Живо ли соединение с Telegram."""
    if client is None:
        return False
    connected = getattr(client, "is_connected", None)
    if callable(connected):
        try:
            return bool(connected())
        except Exception:
            return False
    return bool(getattr(client, "_connected", False))


async def _close_clients(bot, client, ai_session, proxy) -> None:
    """Аккуратно закрывает клиенты Telegram, бота и сессию ИИ."""
    closers = []
    if client is not None:
        closers.append(
            lambda current_client=client: current_client.disconnect()
            if getattr(current_client, "_connected", False)
            else None
        )
    if bot is not None and getattr(bot, "session", None):
        closers.append(lambda current_bot=bot: current_bot.session.close())
    elif proxy is not None and getattr(proxy, "bot_session", None) is not None:
        closers.append(
            lambda current_session=proxy.bot_session: current_session.close()
        )
    if ai_session is not None and not ai_session.closed:
        closers.append(lambda current_ai=ai_session: current_ai.close())

    for closer in closers:
        try:
            res = closer()
            if asyncio.iscoroutine(res):
                await res
        except Exception as cleanup_error:
            print(f"!!! [CLEANUP] Не удалось закрыть ресурс: {cleanup_error}")


async def main() -> None:
    global _last_media_cleanup_ts
    print("=======================================")
    print("     БОТ ЗАПУЩЕН       ")
    print("=======================================")

    try:
        os.makedirs(TEMP_DIR, exist_ok=True)
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR, ignore_errors=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        warn_if_defaults()
    except Exception as startup_error:
        with contextlib.suppress(Exception):
            db.add_log("ERR-100", f"Парсер не смог подготовить окружение: {startup_error}")
        print(f"!!! [ERR-100] Не удалось подготовить окружение парсера: {startup_error}")
        bot_running.clear()
        return

    while True:
        if not bot_running.is_set():
            await _wait_for_resume()

        try:
            cfg = _load_config()
        except Exception as e:
            db.add_log("ERR-100", f"Ошибка чтения настроек из БД: {e}")
            await _wait_for_db()
            continue

        if not cfg["bot_token"] or not cfg["api_id"] or not cfg["api_hash"]:
            await _wait_for_settings()
            continue

        if not _is_valid_bot_token_format(cfg["bot_token"]):
            db.add_log("ERR-202", "Неверный формат токена бота. Проверьте значение bot_token в настройках.")
            print("!!! [ERR-202] Неверный формат токена бота — проверьте настройки и повторите.")
            await asyncio.sleep(SLEEP_NO_CONFIG_S)
            continue

        try:
            proxy = _setup_proxy(cfg)
        except ProxyConfigurationError as proxy_error:
            db.add_log("ERR-302", f"Невозможно запустить парсер: {proxy_error}")
            print(f"!!! [ERR-302] {proxy_error}")
            await asyncio.sleep(SLEEP_NO_CONFIG_S)
            continue

    bot = None
    client = None
    ai_session = None
    proxy = None
    connection_signature = None

    while True:
        if not bot_running.is_set():
            # Парсер остановлен из панели: закрываем соединения и ждём
            # запуска. Файл сессии остаётся целым.
            await _close_clients(bot, client, ai_session, proxy)
            bot = client = ai_session = proxy = None
            connection_signature = None
            await _wait_for_resume()
            continue

        try:
            cfg = _load_config()
        except Exception as e:
            db.add_log("ERR-100", f"Ошибка чтения настроек из БД: {e}")
            await _wait_for_db()
            continue

        if not cfg["bot_token"] or not cfg["api_id"] or not cfg["api_hash"]:
            await _wait_for_settings()
            continue

        if not _is_valid_bot_token_format(cfg["bot_token"]):
            db.add_log("ERR-202", "Неверный формат токена бота. Проверьте значение bot_token в настройках.")
            print("!!! [ERR-202] Неверный формат токена бота — проверьте настройки и повторите.")
            await asyncio.sleep(SLEEP_NO_CONFIG_S)
            continue

        signature = _connection_signature(cfg)
        if (
            client is None
            or signature != connection_signature
            or not _client_is_connected(client)
        ):
            # Повторно используем клиент, пока настройки подключения не изменились
            # и соединение остаётся активным.
            await _close_clients(bot, client, ai_session, proxy)
            bot = client = ai_session = proxy = None
            connection_signature = None

            try:
                proxy = _setup_proxy(cfg)
            except ProxyConfigurationError as proxy_error:
                db.add_log("ERR-302", f"Невозможно запустить парсер: {proxy_error}")
                print(f"!!! [ERR-302] {proxy_error}")
                await asyncio.sleep(SLEEP_NO_CONFIG_S)
                continue

            try:
                bot = Bot(
                    token=cfg["bot_token"],
                    session=proxy.bot_session,
                    default=DefaultBotProperties(parse_mode="HTML"),
                )
                # Сначала повторно отправляем остатки текста после успешной отправки медиа.
                # Повтор не должен приводить к дублированию соответствующих файлов.
                await _retry_pending_text_followups(bot)
                if not bot_running.is_set():
                    continue
                if proxy.mtproto_kwargs:
                    client = TelegramClient(
                        SESSION_PATH,
                        cfg["api_id"],
                        cfg["api_hash"],
                        **proxy.mtproto_kwargs,
                    )
                else:
                    client = TelegramClient(
                        SESSION_PATH,
                        cfg["api_id"],
                        cfg["api_hash"],
                        proxy=proxy.telethon_proxy,
                    )
                try:
                    ai_session = _make_ai_session(
                        proxy, cfg.get("ai_timeout") or AI_REQUEST_TIMEOUT_S
                    )
                except ProxyConfigurationError as ai_proxy_error:
                    # Прокси для запросов к ИИ настроить не удалось. Это
                    # ошибка настройки, а не сбой цикла: сообщаем и ждём.
                    db.add_log("ERR-302", f"Невозможно настроить прокси для ИИ: {ai_proxy_error}")
                    print(f"!!! [ERR-302] {ai_proxy_error}")
                    await asyncio.sleep(SLEEP_NO_CONFIG_S)
                    continue

                session_file = SESSION_PATH + ".session"
                allow_interactive_login = _stdin_is_interactive()
                session_exists = await asyncio.to_thread(os.path.exists, session_file)
                if not session_exists and not allow_interactive_login:
                    db.add_log(
                        "ERR-203",
                        "Сессия Telegram не найдена и интерактивный терминал недоступен. "
                        "Запустите парсер из терминала для создания сессии или положите "
                        "готовый .session в каталог приложения.",
                    )
                    print("!!! [ERR-203] Нужна авторизация Telegram в интерактивном терминале.")
                    bot_running.clear()
                    continue
                if not await _connect_authorized_client(
                    client,
                    allow_interactive=allow_interactive_login,
                ):
                    bot_running.clear()
                    continue
                connection_signature = signature
            except ApiIdInvalidError:
                db.add_log("ERR-201", "Неверный tg_api_id / tg_api_hash. Проверьте my.telegram.org.")
                print("!!! [ERR-201] Критическая ошибка Telegram API.")
                await _close_clients(bot, client, ai_session, proxy)
                bot = client = ai_session = proxy = None
                await asyncio.sleep(SLEEP_API_ERROR_S)
                continue
            except FloodWaitError as fwe:
                db.add_log("ERR-204", f"Telegram выдал FloodWait на {fwe.seconds} сек.")
                print(f"!!! [ERR-204] FloodWait {fwe.seconds} сек...")
                await _close_clients(bot, client, ai_session, proxy)
                bot = client = ai_session = proxy = None
                for _ in range(fwe.seconds):
                    if not bot_running.is_set():
                        break
                    await asyncio.sleep(SLEEP_FLOOD_POLL_S)
                continue
            except Exception as connect_error:
                err_msg = str(connect_error).lower()
                if "proxy" in err_msg or "connection" in err_msg:
                    db.add_log("ERR-301", f"Сеть/прокси: {connect_error}")
                    print("!!! [ERR-301] Прокси-сеть.")
                else:
                    db.add_log("ERR-100", f"Критический сбой подключения: {connect_error}")
                    print(f"!!! КРИТИЧЕСКИЙ СБОЙ: {connect_error}")
                await _close_clients(bot, client, ai_session, proxy)
                bot = client = ai_session = proxy = None
                await asyncio.sleep(SLEEP_API_ERROR_S)
                continue

            # Вход в Telegram выполнен — консоль больше не нужна.
            print("--> [СИСТЕМА] Вход в Telegram выполнен. Окно консоли скрывается, "
                  "приложение продолжает работать.")
            _hide_console_window()

        try:
            print("\n=== КРУГ ОБХОДА ИСТОЧНИКОВ НАЧАТ ===")
            # Отложенные посты публикуются независимо от наличия новых в ленте.
            try:
                due = await _publish_due_scheduled_posts(bot)
                if due:
                    print(f"--> Опубликовано отложенных постов: {due}")
            except Exception as exc:
                db.add_log("ERR-512", f"Не удалось опубликовать отложенные посты: {exc}")
            # Периодическая очистка старых медиафайлов.
            now_ts = time.time()
            if now_ts - _last_media_cleanup_ts > MEDIA_CLEANUP_INTERVAL_HOURS * 3600:
                _last_media_cleanup_ts = now_ts
                try:
                    cleaned = await _cleanup_expired_media()
                    if cleaned:
                        print(f"--> Очищено просроченных медиафайлов: {cleaned}")
                except Exception as exc:
                    db.add_log("WARN-513", f"Автоочистка медиа не удалась: {exc}")
            for source in cfg["sources"]:
                await _process_one_source(client, source, cfg, bot, ai_session)

            if bot_running.is_set():
                print(f"--> Круг завершён. Засыпаю на {cfg['poll_delay']} секунд...")
        except ApiIdInvalidError:
            db.add_log("ERR-201", "Неверный tg_api_id / tg_api_hash. Проверьте my.telegram.org.")
            print("!!! [ERR-201] Критическая ошибка Telegram API.")
            await _close_clients(bot, client, ai_session, proxy)
            bot = client = ai_session = proxy = None
            connection_signature = None
            await asyncio.sleep(SLEEP_API_ERROR_S)
        except FloodWaitError as fwe:
            db.add_log("ERR-204", f"Telegram выдал FloodWait на {fwe.seconds} сек.")
            print(f"!!! [ERR-204] FloodWait {fwe.seconds} сек...")
            for _ in range(fwe.seconds):
                if not bot_running.is_set():
                    break
                await asyncio.sleep(SLEEP_FLOOD_POLL_S)
            continue
        except Exception as loop_error:
            err_msg = str(loop_error).lower()
            if "proxy" in err_msg or "connection" in err_msg:
                db.add_log("ERR-301", f"Сеть/прокси: {loop_error}")
                print("!!! [ERR-301] Прокси-сеть.")
            else:
                db.add_log("ERR-100", f"Критический сбой цикла: {loop_error}")
                print(f"!!! КРИТИЧЕСКИЙ СБОЙ: {loop_error}")
            await asyncio.sleep(SLEEP_API_ERROR_S)

        if not bot_running.is_set():
            continue
        for _ in range(cfg["poll_delay"]):
            if not bot_running.is_set():
                break
            await asyncio.sleep(POLL_PAUSE_S)


# ==========================================
# ЗАПУСК
# ==========================================
def start_api_server() -> None:
    from config import API_HOST, API_LOG_LEVEL, API_PORT
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level=API_LOG_LEVEL)


def open_browser_delayed() -> None:
    time.sleep(BROWSER_OPEN_DELAY_S)
    webbrowser.open(f"http://{API_HOST}:{API_PORT}/")


if __name__ == "__main__":
    from startup import ensure_single_instance, run_startup_bootstrap

    print("--> [СИСТЕМА] Инициализация графического интерфейса и API...")
    run_startup_bootstrap()

    # Вторая копия закрывается: две копии делят файл сессии Telegram, и
    # Telegram признаёт ключ использованным с двух адресов, после чего
    # сессию приходится пересоздавать вручную.
    if not ensure_single_instance():
        raise SystemExit(1)

    threading.Thread(target=start_api_server, daemon=True).start()
    if BROWSER_OPEN_ON_START:
        threading.Thread(target=open_browser_delayed, daemon=True).start()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("--> [СИСТЕМА] Работа завершена пользователем.")
    except SystemExit:
        raise
    except BaseException as top_error:
        with contextlib.suppress(Exception):
            db.add_log("ERR-999", f"Необработанное исключение верхнего уровня: {top_error}")
        print(f"!!! [ERR-999] Критический сбой верхнего уровня: {top_error}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
