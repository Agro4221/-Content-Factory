# ==========================================
# REFACTORED: Core Bot Loop. License checks completely removed.
# ==========================================
# АВТО-УСТАНОВКА БИБЛИОТЕК ПРИ ПЕРВОМ ЗАПУСКЕ
# ==========================================
from deps import check_and_install_dependencies

check_and_install_dependencies()

# ==========================================
# ОСНОВНОЙ КОД БОТА
# ==========================================
import asyncio
import json
import html
import ipaddress
from html.parser import HTMLParser
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
from urllib.parse import quote, urlsplit
from typing import Optional

import aiohttp
import uvicorn
from aiogram import Bot
from aiogram.utils.token import TokenValidationError, validate_token
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import FSInputFile, InputMediaDocument, InputMediaPhoto, InputMediaVideo
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    AuthKeyUnregisteredError,
    SessionPasswordNeededError,
    ChannelInvalidError,
    ChannelPrivateError,
    ChatForbiddenError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.custom.message import Message

from config import (
    AI_REQUEST_TIMEOUT_S,
    AI_TEMPERATURE,
    API_HOST,
    API_PORT,
    BASE_DIR,
    BROWSER_OPEN_DELAY_S,
    BROWSER_OPEN_ON_START,
    MAX_PROXY_PORT,
    MIN_AD_WORD_LEN,
    MIN_PROXY_PORT,
    POLL_DELAY_DEFAULT,
    POLL_DELAY_MAX,
    POLL_DELAY_MIN,
    POLL_PAUSE_S,
    SESSION_PATH,
    SLEEP_API_ERROR_S,
    SLEEP_BETWEEN_POSTS_S,
    SLEEP_DB_ERROR_S,
    SLEEP_FLOOD_POLL_S,
    SLEEP_NO_CONFIG_S,
    TEMP_DIR,
    MODULE_POSTS,
    is_truthy,
    make_post_key,
    normalize_target_chat,
    warn_if_defaults,
)
from database import DatabaseWriteError, get_db
from Site import app
from state import bot_running
from proxy_check import build_mtproto_client_kwargs, format_proxy_host, normalize_mtproto_proxy_link

try:
    from aiohttp_socks import ProxyConnector as SocksConnector
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
    # Telethon normally represents videos as MessageMediaDocument; keep the
    # explicit video name for compatible wrappers and test doubles.
    return type(media).__name__ in {
        "MessageMediaPhoto",
        "MessageMediaDocument",
        "MessageMediaVideo",
    }


def _save_skipped_group(source: str, messages: list[Message], reason: str) -> None:
    """Record all IDs from one logical post together when supported by the DB."""
    post_ids = [message.id for message in messages]
    save_group = getattr(db, "save_skipped_posts", None)
    if callable(save_group):
        save_group(source, post_ids, reason)
        return
    # Compatibility for lightweight adapters and older DB implementations.
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
    """Ensure a Telegram media group uses an API-supported homogeneous family."""
    if len(media_list) < 2:
        return media_list

    has_document = any(isinstance(item, InputMediaDocument) for item in media_list)
    has_visual = any(
        isinstance(item, (InputMediaPhoto, InputMediaVideo))
        for item in media_list
    )
    if has_document and has_visual:
        # Bot API does not allow document items mixed with photo/video items in
        # one album. Sending them all as documents preserves every file safely.
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
    """Choose one safe split point for both the caption and its continuation."""
    if len(text) <= max_chars:
        return len(text)

    def raw_safe_cut(limit: int) -> int:
        prefix = text[:limit]
        space = _last_safe_text_space(prefix)
        if space > 0:
            prefix = prefix[:space]
        cut = len(prefix)

        # Never leave a partial tag or HTML entity at the end of the caption.
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
        # Balancing open HTML tags and escaping plain text can make the rendered
        # prefix longer than its source slice. Account for that here so callers
        # calculating the continuation receive this exact same split point.
        rendered_prefix = _sanitize_telegram_html(text[:cut].rstrip())
        if len(rendered_prefix) <= max_chars:
            return cut
        overflow = len(rendered_prefix) - max_chars
        cut = raw_safe_cut(max(0, cut - max(1, overflow)))
    return 0


def _media_caption_split(text: str, max_chars: int = 1020) -> tuple[str, int]:
    """Return a balanced caption prefix and the exact source index it consumes."""
    caption = (text or "").strip()
    if len(caption) <= max_chars:
        return caption, len(caption)

    cut = _media_caption_cut_index(caption, max_chars)
    while cut > 0:
        raw_prefix = caption[:cut].rstrip()
        prefix = _sanitize_telegram_html(raw_prefix)
        if len(prefix) <= max_chars:
            # Use this final cut for the remainder too. Sanitizing an open HTML
            # fragment may add closing tags and require moving the cut backwards.
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


class ProxyConfigurationError(RuntimeError):
    """Enabled proxy settings cannot be applied safely."""


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
    if user or password:
        auth = f"{quote(str(user), safe='')}:{quote(str(password), safe='')}@"
    else:
        auth = ""

    url_host = format_proxy_host(host)
    proxy_url = f"{proxy_type}://{auth}{url_host}:{port}"
    try:
        return AiohttpSession(proxy=proxy_url)
    except Exception as exc:
        raise ProxyConfigurationError(
            f"Не удалось инициализировать SOCKS-сессию: {exc}"
        ) from exc


def _validate_port(port_str: str) -> Optional[int]:
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
        except ValueError:
            if ":" in host_value or not re.fullmatch(r"[A-Za-z0-9.-]+", host_value):
                raise ValueError(
                    "local_host должен быть корректным именем хоста или IP-адресом."
                )
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
        if (
            parsed_remote_url.scheme.lower() not in {"http", "https"}
            or not parsed_remote_url.hostname
            or not valid_remote_port
        ):
            raise ValueError(
                "Для ai_mode=REMOTE укажите корректный HTTP- или HTTPS-адрес remote_url."
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

    try:
        raw_topics = json.loads(get_setting("topic_channels", "{}") or "{}")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "topic_channels содержит повреждённый JSON; настройки не применены, "
            "чтобы не отправлять посты в неверный канал."
        ) from exc
    if not isinstance(raw_topics, dict):
        raise ValueError(
            "topic_channels должен быть JSON-объектом категорий; настройки не применены."
        )

    topic_channels: dict[str, str] = {}
    for key, value in raw_topics.items():
        if key is None or value is None:
            continue
        norm_key = str(key).strip().lower()
        try:
            norm_val = normalize_target_chat(str(value).strip())
        except ValueError as exc:
            raise ValueError(
                f"Некорректный целевой канал для категории {norm_key!r}: {exc}"
            ) from exc
        if norm_key and norm_val:
            if norm_key in topic_channels:
                raise ValueError(
                    "topic_channels содержит дублирующиеся категории после "
                    f"нормализации регистра: {norm_key!r}. Исправьте маршруты, "
                    "чтобы посты не отправлялись в неверный канал."
                )
            topic_channels[norm_key] = norm_val

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
        "ads_words": ads_words,
        "poll_delay": poll_delay,
        "use_proxy": is_truthy(get_setting("use_proxy", "0")),
        "proxy_type": (get_setting("proxy_type", "") or "").strip().lower(),
        "proxy_host": (get_setting("proxy_host", "") or "").strip(),
        "proxy_port": (get_setting("proxy_port", "") or "").strip(),
        "proxy_user": get_setting("proxy_user", ""),
        "proxy_pass": get_setting("proxy_pass", ""),
        "mtproto_key": get_setting("mtproto_key", ""),
        "watermark": get_setting("watermark_text", ""),
    }


def _setup_proxy(cfg: dict) -> tuple[Optional[dict], Optional[dict], Optional[AiohttpSession]]:
    if not cfg["use_proxy"]:
        return None, None, None

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
    # Respect an explicit UI selection; infer MTProto from its key only for
    # legacy configs whose proxy_type is empty.
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
            "MTProto-прокси применяется только для чтения каналов; бот-отправщик подключается напрямую.",
        )
        return None, mtproto_kwargs, None
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
    else:
        bot_session = _make_socks_session(cfg)

    return telethon_proxy, None, bot_session

# ==========================================
# ОБРАБОТКА ПОСТОВ
# ==========================================
class _TelegramHTMLSanitizer(HTMLParser):
    """Keep only Telegram-supported HTML and always emit balanced, escaped markup."""

    _ALLOWED_TAGS = {
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
        # Telegram HTML does not support comments.
        return

    def result(self) -> str:
        while self.stack:
            tag, rendered = self.stack.pop()
            if rendered:
                self.parts.append(f"</{tag}>")
        return "".join(self.parts)


def _sanitize_telegram_html(text: str) -> str:
    """Preserve supported Telegram formatting while preventing malformed HTML."""
    parser = _TelegramHTMLSanitizer()
    try:
        parser.feed(text or "")
        parser.close()
    except Exception:
        # A malformed HTML fragment must not make the publication loop crash.
        return html.escape(text or "", quote=False)
    return parser.result()


def _truncate_telegram_text(text: str, max_length: int = 4096) -> str:
    """Truncate text while preserving valid Telegram HTML and the full length cap."""
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
        # Closing tags and escaping a partial entity can add characters, so
        # reduce the source slice and retry rather than slicing the sanitized HTML.
        next_limit = max(1, cut - max(8, len(prefix) - content_limit + 8))
        cut = _media_caption_cut_index(text[:cut], next_limit)

    return marker


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
    if raw_text:
        try:
            topic, final_text = await _ai_rewrite(raw_text, cfg, ai_session)
        except AIResponseFormatError as exc:
            db.add_log(
                "WARN-404",
                f"Пост {source}/{messages[0].id} не опубликован: AI вернул неверный формат ({exc}). Оставлен для повтора.",
            )
            print(f"!!! AI вернул неверный формат для {source}/{messages[0].id}; пост оставлен для повтора.")
            return
        except AIRewriteError as exc:
            db.add_log(
                "WARN-405",
                f"Пост {source}/{messages[0].id} не опубликован: AI недоступен ({exc}). Повторим на следующем проходе.",
            )
            print(f"!!! AI-рерайт не удался для {source}/{messages[0].id}; пост оставлен для повтора.")
            return
    else:
        # Media-only source posts have no text to rewrite.
        topic, final_text = "общее", ""

    if cfg["watermark"]:
        final_text = f"{final_text}\n\n{cfg['watermark']}"
    final_text = _sanitize_telegram_html(final_text)

    topic_key = (topic or "").strip().lower()
    target_chat = (
        cfg["topic_channels"].get(topic_key)
        or cfg["topic_channels"].get(topic)
        or cfg["topic_channels"].get("общее")
    )
    if target_chat:
        target_chat = _target_chat_id(target_chat)
    if not target_chat:
        # Missing routing is a configuration problem, not a permanent content
        # skip. Keep the post pending so adding a topic route can recover it.
        db.add_log(
            "WARN-400",
            f"Для темы [{topic}] не настроен целевой канал; пост "
            f"{source}/{messages[0].id} оставлен для повтора. Настройте категорию "
            "или добавьте маршрут 'общее'.",
        )
        print(f"!!! Целевой канал для темы [{topic}] не настроен; пост оставлен для повтора.")
        return

    if not bot_running.is_set():
        return

    try:
        media_list, temp_paths = await _download_media(messages, final_text)
    except FloodWaitError:
        # Let the main loop honor Telegram's required cooldown instead of
        # retrying this download on every polling cycle.
        raise
    except Exception as exc:
        db.add_log(
            "WARN-501",
            f"Пост {source}/{messages[0].id} не опубликован: {exc}. Оставлен для повтора.",
        )
        print(f"!!! Не удалось скачать вложение для {source}/{messages[0].id}; пост оставлен для повтора.")
        return

    MAX_TG_LEN = 4096
    if len(final_text) > MAX_TG_LEN:
        original_len = len(final_text)
        final_text = _truncate_telegram_text(final_text, MAX_TG_LEN)
        db.add_log(
            "WARN-500",
            f"Текст ответа ИИ обрезан с {original_len} до {len(final_text)} символов для Telegram.",
        )

    caption_remainder = _media_caption_remainder(final_text)

    retry_delay_s = 0
    secondary_send_error: Exception | None = None
    try:
        if media_list:
            if len(media_list) == 1:
                single = media_list[0]
                if isinstance(single, InputMediaPhoto):
                    await bot.send_photo(
                        chat_id=target_chat,
                        photo=single.media,
                        caption=single.caption or final_text or None,
                    )
                elif isinstance(single, InputMediaVideo):
                    await bot.send_video(
                        chat_id=target_chat,
                        video=single.media,
                        caption=single.caption or final_text or None,
                    )
                else:
                    await bot.send_document(
                        chat_id=target_chat,
                        document=single.media,
                        caption=single.caption or final_text or None,
                    )
            else:
                safe_media_group = _prepare_media_group(media_list)
                await bot.send_media_group(chat_id=target_chat, media=safe_media_group)

            # The media call above is the primary publication. A separate text
            # suffix is not atomic with it: if the suffix fails, retrying the
            # whole group could publish the media a second time.
            if caption_remainder:
                try:
                    await bot.send_message(chat_id=target_chat, text=caption_remainder)
                except Exception as exc:
                    secondary_send_error = exc
            elif final_text and not any(
                getattr(item, "caption", None) for item in media_list
            ):
                try:
                    await bot.send_message(chat_id=target_chat, text=final_text)
                except Exception as exc:
                    secondary_send_error = exc
        else:
            await bot.send_message(chat_id=target_chat, text=final_text)

    except Exception as send_error:
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
            retry_delay_s = max(0, int(getattr(send_error, "retry_after", 0)))
            db.add_log(
                "WARN-406",
                f"Пост {source}/{messages[0].id} не отправлен из-за временной ошибки "
                f"({type(send_error).__name__}); оставлен для повторной попытки.",
            )
        else:
            # A send rejection can be corrected (permissions, target, token, or
            # malformed payload). Never turn a delivery failure into a permanent
            # skip: stop the loop and leave the post pending for a deliberate retry.
            bot_running.clear()
            db.add_log(
                "ERR-408",
                f"Пост {source}/{messages[0].id} не доставлен из-за постоянной ошибки "
                f"({type(send_error).__name__}); пост оставлен для повтора, парсер остановлен. "
                "Исправьте токен/права/целевой канал и запустите парсер снова.",
            )
            print(
                f"!!! [ERR-408] Отправка поста {source}/{messages[0].id} отклонена; "
                "пост сохранён для повтора, парсер остановлен."
            )
    else:
        if secondary_send_error is not None:
            db.add_log(
                "WARN-407",
                f"Основная публикация {source}/{messages[0].id} уже отправлена в {target_chat}, "
                f"но остаток текста не доставлен ({type(secondary_send_error).__name__}: "
                f"{secondary_send_error}). Пост будет помечен опубликованным, "
                "чтобы повтор не продублировал медиа.",
            )
        failed_post_id = messages[0].id
        try:
            # The real DB commits album/message-group deduplication records
            # atomically. Keep the fallback for lightweight integrations and
            # older test doubles that only implement save_post().
            save_group = getattr(db, "save_posts", None)
            if callable(save_group):
                save_group(source, [message.id for message in messages])
            else:
                for m in messages:
                    failed_post_id = m.id
                    db.save_post(source, m.id)
        except DatabaseWriteError as db_error:
            # Telegram has already accepted the send. Stop before more posts can
            # be emitted with missing deduplication records.
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
            db.add_log(
                "OK-501",
                f"Пост {source}/{messages[0].id} успешно опубликован в {target_chat} "
                f"(тема: {topic}).",
            )
            print(f"УСПЕХ: → [{topic}] -> {target_chat}")
    finally:
        for p in temp_paths:
            try:
                os.remove(p)
            except OSError:
                pass
    await asyncio.sleep(max(SLEEP_BETWEEN_POSTS_S, retry_delay_s))


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
            temp_paths.append(path)
            m_t = _classify_extension(path)
            cap = cap_text if cap_text and not caption_assigned else None
            if cap is not None:
                caption_assigned = True
            media_list.append(m_t(media=FSInputFile(path), caption=cap))

    except BaseException:
        # The caller cannot clean these files if this function never returns.
        for path in temp_paths:
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    print()
    return media_list, temp_paths


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


async def _ai_rewrite(raw_text: str, cfg: dict, session_ai: aiohttp.ClientSession) -> tuple[str, str]:
    topics = ", ".join(cfg["topic_channels"].keys()) or "общее"
    db_prompt = db.get_setting("system_prompt", "")
    if db_prompt:
        s_prompt = f"{db_prompt}\n\nДоступные темы: [{topics}]"
    else:
        s_prompt = (
            "Ты — опытный SMM-редактор. Твоя задача: сделать увлекательный ПЕРЕСКАЗ новости.\n"
            "ПРАВИЛА:\n"
            "1. Выбери ОДНУ тему из списка: [{topics}]\n"
            "2. Если в тексте есть прямая речь или важная фраза — ОБЯЗАТЕЛЬНО оформи "
            "ёё тегом <blockquote>текст</blockquote>.\n"
            "3. Придумай 2-3 релевантных хэштега.\n"
            "ОТВЕТЬ СТРОГО ПО ШАБЛОНУ:\n"
            "ТЕМА: [название]\n"
            "ТЕКСТ: [твой пересказ]\n"
            "ХЭШТЕГИ: [теги]"
        ).format(topics=topics)

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
    try:
        async with session_ai.post(url, json=payload, headers=headers,
                                   timeout=AI_REQUEST_TIMEOUT_S) as r:
            if r.status == 200:
                data = await r.json()
                res = ""
                for item in data.get("choices", []):
                    candidate = item.get("message", {}).get("content", "")
                    if isinstance(candidate, str) and candidate.strip():
                        res = candidate
                        break
                if not res:
                    failure_reason = "AI вернул пустой ответ"
                    db.add_log("ERR-400", failure_reason)
                    print("!!! [AI ERROR] Пустой ответ от AI endpoint")
                else:
                    topic, rewritten_text = _parse_ai_response(res, raw_text)
                    if rewritten_text.strip():
                        return topic, rewritten_text
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
    except aiohttp.ClientConnectorError as e:
        _log_ai_network_error(e, cfg)
        failure_reason = "AI endpoint недоступен"
    except Exception as e:
        db.add_log("ERR-400", f"Неизвестный сбой AI: {e}")
        print(f"!!! [AI ERROR] {e}")
        failure_reason = f"ошибка ответа AI ({type(e).__name__})"

    # Не публикуем обрезок оригинала: вызывающий код оставит запись для повтора.
    raise AIRewriteError(failure_reason)


def _parse_ai_response(res: str, raw_text: str) -> tuple[str, str]:
    if not isinstance(res, str) or not res.strip():
        raise AIResponseFormatError("пустой ответ")
    t_m = re.search(r"ТЕМА:\s*(.*)", res, re.I)
    c_m = re.search(r"ТЕКСТ:\s*(.*)", res, re.S | re.I)
    h_m = re.search(r"ХЭШТЕГИ:\s*(.*)", res, re.I)

    if not c_m:
        raise AIResponseFormatError("отсутствует обязательный раздел ТЕКСТ:")
    topic = (t_m.group(1).strip().lower() if t_m else "общее").split("\n")[0].strip()
    full = c_m.group(1).strip()
    clean = re.split(r"ХЭШТЕГИ\s*:", full, maxsplit=1, flags=re.I)[0].strip()
    if not clean:
        raise AIResponseFormatError("раздел ТЕКСТ: пуст")
    tags = h_m.group(1).strip() if h_m else ""
    return topic, f"{clean}\n\n{tags}" if tags else clean


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


def _stdin_is_interactive() -> bool:
    """Return whether a phone/code login prompt can be safely shown to the user."""
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, OSError, ValueError):
        return False


async def _connect_authorized_client(
    client: TelegramClient,
    *,
    allow_interactive: bool = False,
) -> bool:
    """Connect non-interactively by default; login prompts require an explicit TTY opt-in."""
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
        # Earlier versions stored the exact user-entered link as the cursor
        # key (for example, https://t.me/Channel/123). Runtime config now uses
        # canonical @usernames/peer IDs; keep the old cursor so upgrading does
        # not accidentally trigger the "first scan: latest ten posts" baseline.
        try:
            source_key = normalize_target_chat(source_key) or source_key
        except ValueError:
            # A stale cursor for a removed/malformed source should not block
            # unrelated channels. It simply will not match a valid configured source.
            pass

        previous = normalized.get(source_key)
        # If legacy aliases collide, rewind to the earliest known cursor. Already
        # published IDs are deduplicated in SQLite; choosing the later cursor could
        # skip posts that were pending when the aliases diverged.
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
            # Keep the established first-scan behaviour: seed from the latest ten
            # posts rather than publishing an entire historical channel.
            async for message in client.iter_messages(_source_entity(source), limit=10):
                source_messages.append(message)
            source_messages.sort(key=lambda message: message.id)

            # The oldest of those ten may be in the middle of an album. Pull
            # the remaining messages with that grouped_id before setting the
            # initial cursor, otherwise older album items are never revisited.
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
            # Subsequent scans catch every newer message, not just the newest ten.
            async for message in client.iter_messages(_source_entity(source), min_id=cursor, reverse=True):
                source_messages.append(message)
    except (AuthKeyUnregisteredError, SessionPasswordNeededError) as e:
        db.add_log("ERR-203", f"Сессия Telegram невалидна ({type(e).__name__}): {e}")
        print(f"!!! [ERR-203] Сессия Telegram закрыта / требует 2FA: {e}")
        # Reconnecting with the same revoked or 2FA-blocked session cannot fix
        # this state. Stop the parser until the user supplies a valid session.
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
        # Propagate to main(), which sleeps for the server-provided duration.
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
        previous_cursor = cursor
        while cursor_index < len(source_messages):
            message = source_messages[cursor_index]
            gid = message.grouped_id or f"single_{message.id}"
            if not completed_groups.get(gid, False):
                break
            cursor = message.id
            cursor_index += 1
        if cursor > previous_cursor:
            _set_source_cursor(source, cursor)

    for gid, messages in grouped.items():
        if not bot_running.is_set():
            break

        pending = [
            message for message in messages
            if not db.is_published(source, message.id)
            and not db.is_skipped(source, message.id)
        ]
        if pending:
            await _process_message_group(
                client, source, pending, cfg, bot, ai_session
            )

        completed_groups[gid] = all(
            db.is_published(source, message.id)
            or db.is_skipped(source, message.id)
            for message in messages
        )
        advance_cursor()
        if not completed_groups[gid]:
            # Keep this message available for retry and don't advance over newer IDs.
            return False
    return False



async def main() -> None:
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
        try:
            db.add_log("ERR-100", f"Парсер не смог подготовить окружение: {startup_error}")
        except Exception:
            pass
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
            telethon_proxy, mtproto_kwargs, bot_session = _setup_proxy(cfg)
        except ProxyConfigurationError as proxy_error:
            db.add_log("ERR-302", f"Невозможно запустить парсер: {proxy_error}")
            print(f"!!! [ERR-302] {proxy_error}")
            await asyncio.sleep(SLEEP_NO_CONFIG_S)
            continue

        bot = None
        client = None
        ai_session = None
        need_reconnect = False
        try:
            bot = Bot(
                token=cfg["bot_token"],
                session=bot_session,
                default=DefaultBotProperties(parse_mode="HTML"),
            )
            if mtproto_kwargs:
                client = TelegramClient(
                    SESSION_PATH,
                    cfg["api_id"],
                    cfg["api_hash"],
                    **mtproto_kwargs,
                )
            else:
                client = TelegramClient(
                    SESSION_PATH,
                    cfg["api_id"],
                    cfg["api_hash"],
                    proxy=telethon_proxy,
                )
            ai_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=AI_REQUEST_TIMEOUT_S)
            )

            session_file = SESSION_PATH + ".session"
            allow_interactive_login = _stdin_is_interactive()
            if not os.path.exists(session_file) and not allow_interactive_login:
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
            print("\n=== КРУГ ОБХОДА ИСТОЧНИКОВ НАЧАТ ===")
            for source in cfg["sources"]:
                if await _process_one_source(client, source, cfg, bot, ai_session):
                    need_reconnect = True
                    break

            if bot_running.is_set() and not need_reconnect:
                print(f"--> Круг завершён. Засыпаю на {cfg['poll_delay']} секунд...")
        except ApiIdInvalidError:
            db.add_log("ERR-201", "Неверный tg_api_id / tg_api_hash. Проверьте my.telegram.org.")
            print("!!! [ERR-201] Критическая ошибка Telegram API.")
            await asyncio.sleep(SLEEP_API_ERROR_S)
        except FloodWaitError as fwe:
            db.add_log("ERR-204", f"Telegram выдал FloodWait на {fwe.seconds} сек.")
            print(f"!!! [ERR-204] FloodWait {fwe.seconds} сек...")
            for _ in range(fwe.seconds):
                if not bot_running.is_set():
                    break
                await asyncio.sleep(SLEEP_FLOOD_POLL_S)
            if not bot_running.is_set():
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
        finally:
            closers = []
            if client is not None:
                closers.append(
                    lambda: client.disconnect()
                    if getattr(client, "_connected", False)
                    else None
                )
            if bot is not None and getattr(bot, "session", None):
                closers.append(lambda: bot.session.close())
            elif bot_session is not None:
                closers.append(lambda: bot_session.close())
            if ai_session is not None and not ai_session.closed:
                closers.append(lambda: ai_session.close())

            for closer in closers:
                try:
                    res = closer()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as cleanup_error:
                    print(f"!!! [CLEANUP] Не удалось закрыть ресурс: {cleanup_error}")

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
    from config import API_HOST, API_PORT, API_LOG_LEVEL
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level=API_LOG_LEVEL)


def open_browser_delayed() -> None:
    time.sleep(BROWSER_OPEN_DELAY_S)
    webbrowser.open(f"http://{API_HOST}:{API_PORT}/")


if __name__ == "__main__":
    from startup import run_startup_bootstrap

    print("--> [СИСТЕМА] Инициализация графического интерфейса и API...")
    run_startup_bootstrap()

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
        try:
            db.add_log("ERR-999", f"Необработанное исключение верхнего уровня: {top_error}")
        except Exception:
            pass
        print(f"!!! [ERR-999] Критический сбой верхнего уровня: {top_error}")
        import traceback
        traceback.print_exc()
        sys.exit(1)