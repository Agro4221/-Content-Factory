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
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
from urllib.parse import quote
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
    warn_if_defaults,
)
from database import DatabaseWriteError, get_db
from Site import app
from state import bot_running
from proxy_check import build_mtproto_client_kwargs, format_proxy_host

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
    return type(media).__name__ in {"MessageMediaPhoto", "MessageMediaDocument"}


class MediaDownloadError(RuntimeError):
    """A downloadable Telegram attachment was not retrieved successfully."""


def _classify_extension(path: str):
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in ("mp4", "mov", "gif", "mkv", "webm"):
        return InputMediaVideo
    if ext in ("jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"):
        return InputMediaPhoto
    return InputMediaDocument


def _media_caption_remainder(text: str) -> str:
    """Return text omitted from Telegram's media-caption limit."""
    caption = (text or "").strip()
    if len(caption) <= 1024:
        return ""
    displayed_prefix = caption[:1020].rsplit(" ", 1)[0]
    return caption[len(displayed_prefix):].lstrip()


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


def _make_socks_session(cfg: dict) -> Optional[AiohttpSession]:
    if not _HAS_SOCKS:
        db.add_log("ERR-302", "Для SOCKS5 прокси нужен пакет aiohttp-socks. Бот-отправщик работает БЕЗ прокси.")
        return None

    proxy_type = (cfg.get("proxy_type") or "socks5").strip().lower()
    if proxy_type not in ("socks5", "socks4"):
        proxy_type = "socks5"

    host = (cfg.get("proxy_host") or "").strip()
    port = _validate_port(str(cfg.get("proxy_port") or ""))
    if not host or port is None:
        db.add_log("WARN-300", "SOCKS прокси не применён: host/port невалидны.")
        return None

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
    except Exception as e:
        db.add_log("ERR-302", f"Не удалось инициализировать SOCKS сессию: {e}")
        return None


def _validate_port(port_str: str) -> Optional[int]:
    if not port_str or not port_str.isdigit():
        return None
    p = int(port_str)
    return p if MIN_PROXY_PORT <= p <= MAX_PROXY_PORT else None


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
    sources_raw = db.get_setting("sources", "")
    sources = [s.strip() for s in sources_raw.split("\n") if s.strip()] if sources_raw else []

    ads_raw = db.get_setting("ads_words", "")
    ads_words = [w.strip() for w in ads_raw.split("\n") if w.strip()] if ads_raw else []

    try:
        raw_topics = json.loads(db.get_setting("topic_channels", "{}"))
    except (TypeError, ValueError):
        raw_topics = {}
    topic_channels: dict[str, str] = {}
    if isinstance(raw_topics, dict):
        for key, value in raw_topics.items():
            if key is None or value is None:
                continue
            norm_key = str(key).strip().lower()
            norm_val = str(value).strip()
            if norm_key and norm_val:
                topic_channels[norm_key] = norm_val

    return {
        "api_id": _safe_int(db.get_setting("tg_api_id", "0"), 0),
        "api_hash": (db.get_setting("tg_api_hash", "") or "").strip(),
        "bot_token": (db.get_setting("bot_token", "") or "").strip(),
        "ai_mode": (db.get_setting("ai_mode", "LOCAL") or "LOCAL").strip().upper(),
        "local_url": (
            f"http://{db.get_setting('local_host', '127.0.0.1')}:"
            f"{db.get_setting('local_port', '1234')}/v1/chat/completions"
        ),
        "local_model": db.get_setting("local_model", ""),
        "remote_url": (db.get_setting("remote_url", "") or "").strip(),
        "remote_api_key": db.get_setting("remote_api_key", ""),
        "remote_model": db.get_setting("remote_model", ""),
        "sources": sources,
        "topic_channels": topic_channels,
        "ads_words": ads_words,
        "poll_delay": _safe_int(db.get_setting("poll_delay", str(POLL_DELAY_DEFAULT)),
                               POLL_DELAY_DEFAULT),
        "use_proxy": is_truthy(db.get_setting("use_proxy", "0")),
        "proxy_type": (db.get_setting("proxy_type", "") or "").strip().lower(),
        "proxy_host": (db.get_setting("proxy_host", "") or "").strip(),
        "proxy_port": (db.get_setting("proxy_port", "") or "").strip(),
        "proxy_user": db.get_setting("proxy_user", ""),
        "proxy_pass": db.get_setting("proxy_pass", ""),
        "mtproto_key": db.get_setting("mtproto_key", ""),
        "watermark": db.get_setting("watermark_text", ""),
    }


def _setup_proxy(cfg: dict) -> tuple[Optional[dict], Optional[dict], Optional[AiohttpSession]]:
    if not cfg["use_proxy"] or not cfg["proxy_host"]:
        return None, None, None

    port = _validate_port(cfg["proxy_port"])
    if port is None:
        db.add_log("WARN-300", f"Некорректный proxy_port: {cfg['proxy_port']!r}. Прокси отключён.")
        return None, None, None

    bot_session: Optional[AiohttpSession] = None

    if cfg["mtproto_key"]:
        try:
            mtproto_kwargs = build_mtproto_client_kwargs(
                cfg["proxy_host"],
                port,
                cfg["mtproto_key"],
            )
        except ValueError as exc:
            db.add_log("WARN-300", f"MTProto-секрет некорректен: {exc}. Прокси отключён.")
            return None, None, None
        db.add_log("WARN-300", "MTProto применён только к чтению каналов; бот-отправщик идёт напрямую.")
        return None, mtproto_kwargs, bot_session

    proxy_type = cfg["proxy_type"] or "http"
    telethon_proxy = {
        "proxy_type": proxy_type,
        "addr": cfg["proxy_host"],
        "port": port,
        "username": cfg["proxy_user"] or None,
        "password": cfg["proxy_pass"] or None,
    }
    if proxy_type == "http":
        proxy_host = format_proxy_host(cfg["proxy_host"])
        if cfg["proxy_user"] and cfg["proxy_pass"]:
            proxy_user = quote(str(cfg["proxy_user"]), safe="")
            proxy_password = quote(str(cfg["proxy_pass"]), safe="")
            proxy_url = f"http://{proxy_user}:{proxy_password}@{proxy_host}:{port}"
        else:
            proxy_url = f"http://{proxy_host}:{port}"
        bot_session = AiohttpSession(proxy=proxy_url)
    elif proxy_type in ("socks5", "socks4"):
        if not _HAS_SOCKS:
            db.add_log("ERR-302", "Для SOCKS5 нужен aiohttp-socks. Бот-отправщик работает БЕЗ прокси.")
        else:
            bot_session = _make_socks_session(cfg)

    return telethon_proxy, None, bot_session


# ==========================================
# ОБРАБОТКА ПОСТОВ
# ==========================================
def _truncate_telegram_text(text: str, max_length: int = 4096) -> str:
    """Keep as much content as possible while respecting Telegram text limits."""
    marker = "\n\n[…обрезано…]"
    if len(text) <= max_length:
        return text

    content_limit = max(max_length - len(marker), 0)
    prefix = text[:content_limit]
    last_space = prefix.rfind(" ")
    if last_space > 0:
        prefix = prefix[:last_space]
    else:
        # A URL/token may not contain spaces; hard-cut it rather than dropping it.
        prefix = prefix.rstrip()
    if not prefix and content_limit:
        prefix = text[:content_limit]
    return prefix.rstrip() + marker


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

    has_media = any(not _is_media_skippable(m.media) for m in messages)
    if not raw_text and not has_media:
        for m in messages:
            db.save_skipped_post(source, m.id, "empty")
        return

    if raw_text and cfg["ads_words"] and _contains_ad(raw_text, cfg["ads_words"]):
        for m in messages:
            db.save_skipped_post(source, m.id, "ads_word")
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

    topic_key = (topic or "").strip().lower()
    target_chat = (
        cfg["topic_channels"].get(topic_key)
        or cfg["topic_channels"].get(topic)
        or cfg["topic_channels"].get("общее")
    )
    if not target_chat:
        reason = f"no_target:{topic}"
        for m in messages:
            db.save_skipped_post(source, m.id, reason)
        db.add_log("WARN-400", f"Нет целевого канала для темы [{topic}] — пост {source}/{messages[0].id} помечен как пропущенный.")
        print(f"!!! Целевой канал для темы [{topic}] не настроен. Пост пропущен.")
        return

    if not bot_running.is_set():
        return

    try:
        media_list, temp_paths = await _download_media(messages, final_text)
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
                await bot.send_media_group(chat_id=target_chat, media=media_list)

            if caption_remainder:
                await bot.send_message(chat_id=target_chat, text=caption_remainder)
            elif final_text and not any(
                getattr(item, "caption", None) for item in media_list
            ):
                await bot.send_message(chat_id=target_chat, text=final_text)
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
            for m in messages:
                db.save_skipped_post(source, m.id, "send_failed")
    else:
        failed_post_id = messages[0].id
        try:
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
        cap_text = cap_text[:1020].rsplit(" ", 1)[0] + "…"
    caption_assigned = False
    try:
        for m in messages:
            if _is_media_skippable(m.media):
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
    clean = full.split("ХЭШТЕГИ:")[0].strip()
    if not clean:
        raise AIResponseFormatError("раздел ТЕКСТ: пуст")
    tags = h_m.group(1).strip() if h_m else ""
    return topic, f"{clean}\n\n{tags}"


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


async def _wait_for_db() -> None:
    print("!!! [БАЗА ДАННЫХ] Ошибка загрузки настроек. Повтор через 10 сек.")
    await asyncio.sleep(SLEEP_DB_ERROR_S)


_SOURCE_CURSORS_SETTING = "_internal_source_cursors_json"


def _load_source_cursors() -> dict[str, int]:
    try:
        raw = db.get_setting(_SOURCE_CURSORS_SETTING, "{}")
        cursors = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(cursors, dict):
        return {}
    return {
        source: value
        for source, value in cursors.items()
        if isinstance(source, str)
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }


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
    cursor = _get_source_cursor(source)
    source_messages: list[Message] = []
    try:
        if cursor is None:
            # Keep the established first-scan behaviour: seed from the latest ten
            # posts rather than publishing an entire historical channel.
            async for message in client.iter_messages(source, limit=10):
                source_messages.append(message)
        else:
            # Subsequent scans catch every newer message, not just the newest ten.
            async for message in client.iter_messages(source, min_id=cursor, reverse=True):
                source_messages.append(message)
    except (AuthKeyUnregisteredError, SessionPasswordNeededError) as e:
        db.add_log("ERR-203", f"Сессия Telegram невалидна ({type(e).__name__}): {e}")
        print(f"!!! [ERR-203] Сессия Telegram закрыта / требует 2FA: {e}")
        return True
    except (ChannelInvalidError, UsernameInvalidError, UsernameNotOccupiedError) as e:
        db.add_log("ERR-200", f"Канал не найден {source!r}: {e}")
        print(f"!!! Ошибка доступа к {source}: канал не найден.")
        return False
    except (ChannelPrivateError, ChatForbiddenError) as e:
        db.add_log("ERR-200", f"Нет доступа к каналу {source!r}: {e}")
        print(f"!!! Ошибка доступа к {source}: канал приватный или бот не подписан.")
        return False
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

    os.makedirs(TEMP_DIR, exist_ok=True)
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    warn_if_defaults()

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

        telethon_proxy, mtproto_kwargs, bot_session = _setup_proxy(cfg)

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
        ai_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=AI_REQUEST_TIMEOUT_S))

        need_reconnect = False
        try:
            session_file = SESSION_PATH + ".session"
            if not os.path.exists(session_file):
                db.add_log("ERR-203", "Сессия Telegram не найдена. Запустите парсер в интерактивном режиме один раз для создания сессии, либо положите готовый .session в BASE_DIR.")
                print("!!! [ERR-203] Сессия Telegram не найдена — запуск в headless-режиме невозможен.")
                await asyncio.sleep(SLEEP_API_ERROR_S)
                continue
            await client.start()
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
            for closer in (
                lambda: client.disconnect() if getattr(client, "_connected", False) else None,
                lambda: bot.session.close() if bot and getattr(bot, "session", None) else None,
                lambda: ai_session.close() if (ai_session and not ai_session.closed) else None,
            ):
                try:
                    res = closer()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

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