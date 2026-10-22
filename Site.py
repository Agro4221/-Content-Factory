# Веб-панель и API для сборки контента.
"""
FastAPI-приложение: HTML-админка, JSON API для управления основным ботом публикаций.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urlsplit

import aiohttp
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import api_security
import media_marker
import routing
from config import (
    ADMIN_HTML_PATH,
    AI_REQUEST_TIMEOUT_S,
    AI_TIMEOUT_MAX_S,
    AI_TIMEOUT_MIN_S,
    API_LOG_LEVEL,
    BASE_DIR,
    BUNDLE_DIR,
    DEFAULT_LOCAL_AI_PROVIDER,
    DEFAULT_SYSTEM_PROMPT,
    LOCAL_AI_PROVIDERS,
    LOGS_PAGE_SIZE,
    MAX_PROXY_PORT,
    MIN_PROXY_PORT,
    MODULE_POSTS,
    POLL_DELAY_MAX,
    POLL_DELAY_MIN,
    SECRET_CLEAR_SIGNAL,
    SECRET_MASK,
    SECRET_SETTING_KEYS,
    SETTING_KEYS,
    is_truthy,
    normalize_target_chat,
)
from database import format_display_timestamp, get_db
from deps import check_and_install_dependencies
from proxy_check import (
    check_proxy,
    decode_mtproto_secret_full,
    normalize_mtproto_proxy_link,
    resolve_proxy_type,
)
from state import bot_running

db = get_db()
# Документация API отключена: локальный сервер не должен отдавать схему
# всех эндпоинтов любому процессу и любому сайту из браузера.
app = FastAPI(
    title="Content Factory",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def _local_api_guard(request: Request, call_next):
    """Защита локального API: чужой Host, CSRF, размер тела, заголовки."""
    return await api_security.local_api_guard(request, call_next)


def _is_loopback_host(host: str | None) -> bool:
    """Return True only for an unambiguously local bind/client address."""
    normalized = (host or "").strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        # Hostnames other than the literal localhost are treated as exposed:
        # do not rely on DNS resolution to decide whether authentication is needed.
        return False


_STATIC_ASSETS: dict[str, str] = {
    "favicon.png": "image/png",
    "brand-logo.webp": "image/webp",
    "brand-logo.png": "image/png",
    "brand-logo@2x.webp": "image/webp",
    "brand-logo@2x.png": "image/png",
}


def _register_static_assets(application: FastAPI) -> None:
    """Отдаём логотип и favicon по путям из HTML."""
    for filename, media_type in _STATIC_ASSETS.items():
        filepath = os.path.join(BUNDLE_DIR, filename)
        if not os.path.isfile(filepath):
            filepath = os.path.join(BASE_DIR, filename)

        async def _serve_asset(
            _filepath: str = filepath,
            _media_type: str = media_type,
        ) -> FileResponse:
            if not os.path.isfile(_filepath):
                raise HTTPException(status_code=404, detail="Файл не найден")
            return FileResponse(_filepath, media_type=_media_type)

        application.add_api_route(
            f"/{filename}",
            _serve_asset,
            methods=["GET"],
            include_in_schema=False,
        )


_register_static_assets(app)

MODULE_LOG_PREFIX: dict[str, tuple[str, ...]] = {
    MODULE_POSTS: ("ERR-", "WARN-", "SYS-", "OK", "SUCCESS"),
}

MODULE_ORDER: tuple[str, ...] = (MODULE_POSTS,)

MODULE_RUNTIME_META: dict[str, dict[str, object]] = {
    MODULE_POSTS: {
        "name": "Публикация постов",
        "available": True,
        "in_development": False,
        "simulation_mode": False,
        "description": "Парсинг Telegram-каналов, AI-обработка и публикация в целевой канал.",
    },
}


class SettingsValidationError(Exception):
    def __init__(self, field: str, msg: str) -> None:
        super().__init__(msg)
        self.field = field
        self.msg = msg


def _normalize_int_range(
    field: str,
    raw: str,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
    allow_empty: bool = False,
) -> str:
    if raw == "":
        if allow_empty:
            return ""
        raise SettingsValidationError(field=field, msg=f"{field} не может быть пустым")
    if not raw.isascii() or not raw.isdigit():
        raise SettingsValidationError(field=field, msg=f"{field} должен быть целым числом")
    # Python limits decimal-to-int conversion length to mitigate denial of
    # service. Return a field validation error rather than leaking that ValueError
    # as an HTTP 500 when an API client submits thousands of digits.
    if len(raw) > 20:
        raise SettingsValidationError(
            field=field,
            msg=f"{field} содержит слишком много цифр.",
        )
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsValidationError(
            field=field,
            msg=f"{field} должен быть допустимым целым числом.",
        ) from exc
    if min_value is not None and value < min_value:
        raise SettingsValidationError(field=field, msg=f"{field} должен быть >= {min_value}")
    if max_value is not None and value > max_value:
        raise SettingsValidationError(field=field, msg=f"{field} должен быть <= {max_value}")
    return str(value)


def _normalize_validated_setting(key: str, value) -> str:
    """Валидирует оставшиеся глобальные ключи настроек."""
    raw = "" if value is None else str(value).strip()

    if key == "ai_mode":
        normalized = raw.upper()
        if normalized in {"LOCAL", "REMOTE"}:
            return normalized
        raise SettingsValidationError(
            field=key,
            msg=f"{key} должен быть LOCAL или REMOTE",
        )

    if key == "sources":
        normalized_sources: list[str] = []
        for raw_source in raw.splitlines():
            source = raw_source.strip()
            if not source:
                continue
            try:
                source = normalize_target_chat(source)
            except ValueError as exc:
                raise SettingsValidationError(
                    field=key,
                    msg=f"Некорректный источник {raw_source.strip()!r}: {exc}",
                ) from exc
            if source and source not in normalized_sources:
                normalized_sources.append(source)
        return "\n".join(normalized_sources)

    if key == "topic_channels":
        try:
            topics = json.loads(raw or "{}")
        except (TypeError, ValueError) as exc:
            raise SettingsValidationError(
                field=key,
                msg="topic_channels должен содержать корректный JSON-объект категорий.",
            ) from exc
        if not isinstance(topics, dict):
            raise SettingsValidationError(
                field=key,
                msg="topic_channels должен быть JSON-объектом, а не массивом или скаляром.",
            )
        normalized_topics: dict[str, str] = {}
        seen_topic_keys: set[str] = set()
        for topic, target in topics.items():
            if not isinstance(topic, str) or not isinstance(target, str):
                raise SettingsValidationError(
                    field=key,
                    msg="Названия категорий и целевые каналы должны быть строками.",
                )
            topic = topic.strip()
            try:
                target = normalize_target_chat(target)
            except ValueError as exc:
                raise SettingsValidationError(field=key, msg=str(exc)) from exc
            if topic and target:
                canonical_topic = topic.lower()
                if canonical_topic in seen_topic_keys:
                    raise SettingsValidationError(
                        field=key,
                        msg=(
                            "Категории не должны повторяться с разным регистром: "
                            f"{topic!r} конфликтует с существующей категорией."
                        ),
                    )
                seen_topic_keys.add(canonical_topic)
                normalized_topics[topic] = target
        return json.dumps(normalized_topics, ensure_ascii=False)

    if key == "tag_routes":
        # Маршруты по тегам: канал → список тегов. Один и тот же тег может
        # стоять у нескольких каналов — тогда пост уйдёт во все.
        try:
            raw_routes = json.loads(raw or "{}")
        except (TypeError, ValueError) as exc:
            raise SettingsValidationError(
                field=key,
                msg="Маршруты должны содержать корректный JSON-объект.",
            ) from exc
        if not isinstance(raw_routes, dict):
            raise SettingsValidationError(
                field=key,
                msg="Маршруты должны быть JSON-объектом «канал → теги».",
            )
        normalized_routes: dict[str, list[str]] = {}
        for channel, tags in raw_routes.items():
            if not isinstance(channel, str):
                raise SettingsValidationError(
                    field=key,
                    msg="Названия каналов в маршрутах должны быть строками.",
                )
            try:
                target = normalize_target_chat(channel.strip())
            except ValueError as exc:
                raise SettingsValidationError(field=key, msg=str(exc)) from exc
            tag_list = routing.parse_tag_list(tags)
            if not target or not tag_list:
                continue
            if target in normalized_routes:
                for tag in tag_list:
                    if tag not in normalized_routes[target]:
                        normalized_routes[target].append(tag)
            else:
                normalized_routes[target] = tag_list
        return routing.routes_to_json(normalized_routes)

    if key == "proxy_type":
        normalized = raw.lower()
        if not normalized:
            return ""
        if normalized in {"http", "socks5", "socks4", "mtproto"}:
            return normalized
        raise SettingsValidationError(
            field=key,
            msg="proxy_type должен быть одним из: HTTP, SOCKS5, SOCKS4, MTProto",
        )

    if key == "use_proxy":
        normalized = raw.lower()
        if normalized in {"1", "true", "yes", "on"}:
            return "1"
        if normalized in {"0", "false", "no", "off"}:
            return "0"
        raise SettingsValidationError(
            field=key,
            msg="use_proxy должен быть логическим значением",
        )

    if key == "poll_delay":
        return _normalize_int_range(
            "poll_delay",
            raw,
            min_value=POLL_DELAY_MIN,
            max_value=POLL_DELAY_MAX,
        )

    if key == "ai_timeout":
        if not raw:
            # Пустое поле = «использовать значение приложения по умолчанию».
            # Значение по умолчанию намеренно не записывается в базу: иначе
            # после первого сохранения настройка из .env перестала бы влиять.
            return ""
        return _normalize_int_range(
            "ai_timeout",
            raw,
            min_value=AI_TIMEOUT_MIN_S,
            max_value=AI_TIMEOUT_MAX_S,
        )

    if key == "local_ai_provider":
        normalized = raw.strip().lower()
        if not normalized:
            return ""
        if normalized in LOCAL_AI_PROVIDERS:
            return normalized
        raise SettingsValidationError(
            field=key,
            msg="Выберите провайдера из списка: " + ", ".join(sorted(LOCAL_AI_PROVIDERS)) + ".",
        )

    if key == "local_port":
        return _normalize_int_range(
            "local_port",
            raw,
            min_value=1,
            max_value=65535,
        )

    if key == "local_host":
        if not raw:
            return "127.0.0.1"
        if any(char in raw for char in "/?#@") or any(char.isspace() for char in raw):
            raise SettingsValidationError(
                field=key,
                msg="local_host должен быть именем хоста или IP-адресом, без URL-схемы и пути.",
            )
        bracketed = raw.startswith("[") or raw.endswith("]")
        host_value = raw[1:-1] if raw.startswith("[") and raw.endswith("]") else raw
        if bracketed:
            try:
                if ipaddress.ip_address(host_value).version != 6:
                    raise ValueError("brackets are only valid for IPv6")
            except ValueError as exc:
                raise SettingsValidationError(
                    field=key,
                    msg="В квадратных скобках должен быть корректный IPv6-адрес.",
                ) from exc
        else:
            try:
                ipaddress.ip_address(host_value)
            except ValueError as exc:
                if ":" in host_value or not re.fullmatch(r"[A-Za-z0-9.-]+", host_value):
                    raise SettingsValidationError(
                        field=key,
                        msg="local_host должен быть корректным именем хоста или IP-адресом.",
                    ) from exc
        return raw

    if key == "remote_url":
        if not raw:
            return ""
        try:
            parsed_url = urlsplit(raw)
            valid_port = parsed_url.port is None or 1 <= parsed_url.port <= 65535
        except ValueError as exc:
            raise SettingsValidationError(
                field=key,
                msg="remote_url содержит некорректный адрес или порт.",
            ) from exc
        scheme = parsed_url.scheme.lower()
        host = parsed_url.hostname
        if (
            scheme not in {"http", "https"}
            or not host
            or not valid_port
            or (scheme == "http" and not _is_loopback_host(host))
        ):
            raise SettingsValidationError(
                field=key,
                msg=(
                    "remote_url должен использовать HTTPS; HTTP разрешён только "
                    "для localhost или loopback-адреса."
                ),
            )
        return raw

    if key == "proxy_port":
        return _normalize_int_range(
            "proxy_port",
            raw,
            min_value=MIN_PROXY_PORT,
            max_value=MAX_PROXY_PORT,
            allow_empty=True,
        )

    if key == "tg_api_id":
        return _normalize_int_range("tg_api_id", raw, min_value=1, allow_empty=True)

    return "" if value is None else str(value)


async def _read_json_object(request: Request) -> dict:
    """Parse a JSON object and return a client error for malformed input."""
    try:
        data = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Некорректный JSON") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Ожидается JSON-объект")
    return data


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    if not os.path.exists(ADMIN_HTML_PATH):
        return HTMLResponse(
            content="<h1>Ошибка: admin.html не найден рядом с исполняемым файлом</h1>",
            status_code=500,
        )
    with open(ADMIN_HTML_PATH, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# === Управление модульным рантаймом ===
def _module_runtime_meta(module_id: str) -> dict[str, object]:
    meta = MODULE_RUNTIME_META.get(module_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Неизвестный модуль: {module_id}")
    return meta


def _module_is_running(module_id: str) -> bool:
    if module_id == MODULE_POSTS:
        return bot_running.is_set()
    return False


def _module_status_payload(module_id: str) -> dict[str, object]:
    meta = _module_runtime_meta(module_id)
    running = _module_is_running(module_id)
    status = "running" if running else "stopped"
    return {
        "id": module_id,
        "name": str(meta.get("name", module_id)),
        "status": status,
        "state": status,
        "runtime_state": None,
        "running": running,
        "available": bool(meta.get("available")),
        "in_development": bool(meta.get("in_development")),
        "last_error": None,
    }


def _build_modules_payload() -> dict[str, object]:
    modules = [_module_status_payload(module_id) for module_id in MODULE_ORDER]
    available_modules = [module for module in modules if module["available"]]
    running_active = [module for module in available_modules if module["running"]]

    active_total = len(available_modules)
    running_active_total = len(running_active)

    return {
        "modules": modules,
        "summary": {
            "total_modules": len(modules),
            "active_modules": active_total,
            "running_modules": running_active_total,
            "running_active_modules": running_active_total,
            "all_active_running": bool(active_total) and running_active_total == active_total,
            "any_running": running_active_total > 0,
            "mixed": False,
            "no_active_modules": active_total == 0,
        },
    }


def _start_module(module_id: str) -> bool:
    meta = _module_runtime_meta(module_id)
    if not bool(meta.get("available")):
        return False

    if module_id == MODULE_POSTS:
        if bot_running.is_set():
            return False
        bot_running.set()
        db.add_log("SYS-000", "Цикл парсера запущен пользователем.")
        return True

    raise HTTPException(status_code=404, detail=f"Неизвестный модуль: {module_id}")


def _stop_module(module_id: str) -> bool:
    if module_id == MODULE_POSTS:
        if not bot_running.is_set():
            return False
        bot_running.clear()
        db.add_log("SYS-000", "Цикл парсера остановлен пользователем.")
        return True

    raise HTTPException(status_code=404, detail=f"Неизвестный модуль: {module_id}")


@app.get("/api/modules/status")
async def api_modules_status() -> dict:
    return _build_modules_payload()


@app.post("/api/modules/start-all")
async def api_modules_start_all() -> dict:
    started: list[str] = []
    for module_id in MODULE_ORDER:
        if _start_module(module_id):
            started.append(module_id)
    payload = _build_modules_payload()
    payload.update({"status": "success", "started": started, "skipped": []})
    return payload


@app.post("/api/modules/stop-all")
async def api_modules_stop_all() -> dict:
    stopped: list[str] = []
    for module_id in MODULE_ORDER:
        if _stop_module(module_id):
            stopped.append(module_id)
    payload = _build_modules_payload()
    payload.update({"status": "success", "stopped": stopped})
    return payload


@app.post("/api/modules/{module_id}/start")
async def api_module_start(module_id: str) -> dict:
    meta = _module_runtime_meta(module_id)
    if not bool(meta.get("available")):
        payload = _module_status_payload(module_id)
        payload["detail"] = "Модуль временно недоступен."
        return JSONResponse(status_code=409, content=payload)

    started = _start_module(module_id)
    payload = _module_status_payload(module_id)
    payload["started"] = started
    payload["status_code"] = "ok"
    return payload


@app.post("/api/modules/{module_id}/stop")
async def api_module_stop(module_id: str) -> dict:
    _module_runtime_meta(module_id)
    _stop_module(module_id)
    payload = _module_status_payload(module_id)
    payload["status_code"] = "ok"
    return payload


@app.get("/api/bot/status")
async def api_bot_status() -> dict:
    status = _module_status_payload(MODULE_POSTS)
    return {
        "status": status["status"],
        "available": status["available"],
        "in_development": status["in_development"],
    }


@app.post("/api/bot/toggle")
async def api_bot_toggle() -> dict:
    if bot_running.is_set():
        _stop_module(MODULE_POSTS)
    else:
        _start_module(MODULE_POSTS)
    return {"status": "running" if bot_running.is_set() else "stopped"}


# === Настройки ===
@app.get("/api/settings/defaults")
async def api_get_settings_defaults() -> dict:
    """Рекомендуемые значения настроек, которые можно вернуть одним нажатием."""
    return {
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "ai_timeout": AI_REQUEST_TIMEOUT_S,
    }


@app.get("/api/settings")
async def api_get_settings(module: str | None = Query(default=None)) -> dict:
    # Секреты не покидают бэкенд в открытом виде: панель получает маску и
    # может заменить значение или удалить его, но не прочитать текущее.
    masked_getter = getattr(db, "get_masked_settings", None)
    if callable(masked_getter):
        settings = dict(masked_getter())
    else:
        settings = dict(db.get_all_settings())
    settings["use_proxy"] = "1" if is_truthy(settings.get("use_proxy", "0")) else "0"
    return settings


PROXY_SETTING_KEYS = frozenset(
    {"use_proxy", "proxy_type", "proxy_host", "proxy_port", "proxy_user", "proxy_pass", "mtproto_key"}
)

_proxy_runtime_status: dict = {
    "state": "unknown",
    "reachable": False,
    "proxy_type": "",
    "message": "",
    "checked_at": None,
}

# Как долго кэшируется результат проверки прокси для карточки статуса.
PROXY_STATUS_TTL = 30.0


def _proxy_cfg_from_settings(settings: dict | None = None) -> dict:
    src = settings if settings is not None else db.get_all_settings()
    return {
        "use_proxy": src.get("use_proxy", "0"),
        "proxy_type": src.get("proxy_type", ""),
        "proxy_host": src.get("proxy_host", ""),
        "proxy_port": src.get("proxy_port", ""),
        "proxy_user": src.get("proxy_user", ""),
        "proxy_pass": src.get("proxy_pass", ""),
        "mtproto_key": src.get("mtproto_key", ""),
    }


def get_proxy_runtime_status() -> dict:
    return dict(_proxy_runtime_status)


async def refresh_proxy_runtime_status(settings: dict | None = None) -> dict:
    cfg = _proxy_cfg_from_settings(settings)
    use_proxy = is_truthy(cfg.get("use_proxy", "0"))
    host = (cfg.get("proxy_host") or "").strip()

    if not use_proxy:
        result = {
            "ok": True,
            "reachable": False,
            "state": "disabled",
            "message": "Прокси выключен",
            "proxy_type": "",
        }
    elif not host:
        result = {
            "ok": False,
            "reachable": False,
            "state": "no_host",
            "message": "Укажите хост прокси",
            "proxy_type": "",
        }
    else:
        _proxy_runtime_status.update(
            {
                "state": "checking",
                "reachable": False,
                "proxy_type": resolve_proxy_type(cfg).upper(),
                "message": "Проверка соединения…",
                "checked_at": time.time(),
            }
        )
        result = await check_proxy(cfg)

    _proxy_runtime_status.update(
        {
            "state": result.get("state", "unknown"),
            "reachable": bool(result.get("reachable")),
            "proxy_type": result.get("proxy_type") or "",
            "message": result.get("message") or "",
            "checked_at": time.time(),
        }
    )
    return get_proxy_runtime_status()


@app.on_event("startup")
async def _on_startup_refresh_proxy() -> None:
    asyncio.create_task(refresh_proxy_runtime_status())


@app.post("/api/settings")
async def api_post_settings(request: Request, background_tasks: BackgroundTasks) -> dict:
    data = await _read_json_object(request)

    rejected: list[str] = []
    accepted: dict[str, str] = {}
    kept_secrets: list[str] = []
    for key, value in data.items():
        if key not in SETTING_KEYS:
            rejected.append(key)
            continue

        if key in SECRET_SETTING_KEYS:
            raw = "" if value is None else str(value)
            if raw == SECRET_CLEAR_SIGNAL:
                # Явный запрос удалить сохранённый секрет.
                accepted[key] = ""
                continue
            if raw == "" or raw == SECRET_MASK:
                # Пустое поле или маска = «оставить сохранённое значение».
                kept_secrets.append(key)
                continue

        try:
            accepted[key] = _normalize_validated_setting(key, value)
        except SettingsValidationError as e:
            return JSONResponse(
                status_code=422,
                content={"status": "error", "field": e.field, "msg": e.msg},
            )

    if accepted.keys() & PROXY_SETTING_KEYS:
        effective_settings = db.get_all_settings()
        effective_settings.update(accepted)
        proxy_enabled = str(effective_settings.get("use_proxy", "0") or "").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if proxy_enabled:
            try:
                normalized_proxy_settings = normalize_mtproto_proxy_link(effective_settings)
            except ValueError as exc:
                return JSONResponse(
                    status_code=422,
                    content={"status": "error", "field": "proxy_host", "msg": str(exc)},
                )
            # When an MTProto URL was pasted into the host field, persist its
            # actual server, port, and secret atomically with the rest of the form.
            for key in ("proxy_host", "proxy_port", "mtproto_key", "proxy_type"):
                if normalized_proxy_settings.get(key) != effective_settings.get(key):
                    effective_settings[key] = normalized_proxy_settings.get(key, "")
                    accepted[key] = str(effective_settings[key] or "")
            if not (effective_settings.get("proxy_host") or "").strip():
                return JSONResponse(
                    status_code=422,
                    content={
                        "status": "error",
                        "field": "proxy_host",
                        "msg": "Укажите хост прокси, прежде чем включать прокси.",
                    },
                )
            proxy_port = (effective_settings.get("proxy_port") or "").strip()
            try:
                _normalize_int_range(
                    "proxy_port",
                    proxy_port,
                    min_value=MIN_PROXY_PORT,
                    max_value=MAX_PROXY_PORT,
                )
            except (SettingsValidationError, ValueError):
                return JSONResponse(
                    status_code=422,
                    content={
                        "status": "error",
                        "field": "proxy_port",
                        "msg": "Укажите корректный порт прокси в диапазоне 1–65535.",
                    },
                )

            if resolve_proxy_type(effective_settings) == "mtproto":
                secret = (effective_settings.get("mtproto_key") or "").strip()
                if not secret:
                    return JSONResponse(
                        status_code=422,
                        content={
                            "status": "error",
                            "field": "mtproto_key",
                            "msg": "Для MTProto-прокси необходимо указать секретный ключ.",
                        },
                    )
                try:
                    decode_mtproto_secret_full(secret)
                except (ValueError, TypeError) as exc:
                    return JSONResponse(
                        status_code=422,
                        content={
                            "status": "error",
                            "field": "mtproto_key",
                            "msg": f"Секрет MTProto некорректен: {exc}",
                        },
                    )

    # Секрет мог быть восстановлен из MTProto-ссылки при нормализации —
    # в таком случае он сохранён, и в ответе не должен попасть в «kept».
    if kept_secrets:
        kept_secrets = [key for key in kept_secrets if key not in accepted]

    if accepted:
        save_settings_batch = getattr(db, "set_settings", None)
        try:
            if callable(save_settings_batch):
                save_settings_batch(accepted)
            else:
                # Compatibility for adapters/test doubles predating set_settings().
                for key, normalized_value in accepted.items():
                    db.set_setting(key, normalized_value)
        except Exception:
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "msg": "Не удалось сохранить настройки. Изменения отменены; проверьте доступность базы данных.",
                },
            )

    saved = len(accepted)
    if accepted.keys() & PROXY_SETTING_KEYS:
        background_tasks.add_task(refresh_proxy_runtime_status)
    response: dict = {"status": "success", "saved": saved}
    if kept_secrets:
        # Панель сообщает, какие секреты остались без изменений. Значения
        # секретов в ответе не участвуют.
        response["kept_secrets"] = kept_secrets
    if rejected:
        response["status"] = "partial"
        response["rejected_keys"] = rejected
    return response


@app.get("/api/ai/providers")
async def api_ai_providers() -> dict:
    """Список локальных провайдеров AI с их адресами по умолчанию.

    Панель использует значения отсюда, чтобы при выборе провайдера
    автоматически подставлять корректные хост и порт.
    """
    providers = [
        {
            "id": key,
            "label": spec["label"],
            "host": spec["host"],
            "port": spec["port"],
            "hint": spec["hint"],
        }
        for key, spec in LOCAL_AI_PROVIDERS.items()
    ]
    return {"providers": providers, "default": DEFAULT_LOCAL_AI_PROVIDER}


@app.get("/api/ai/models")
async def api_ai_models(
    provider: str = Query(default="ollama"),
    port: int | None = Query(default=None, ge=1, le=65535),
    host: str = Query(default="127.0.0.1"),
) -> dict:
    """List models from a provider on loopback, using a validated port.

    The host is deliberately fixed to loopback so this endpoint cannot become
    a generic server-side URL fetch. Remote model servers require manual names.
    """
    provider_name = (provider or "").strip().lower()
    if provider_name not in LOCAL_AI_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail="Неизвестный провайдер. Доступны: " + ", ".join(sorted(LOCAL_AI_PROVIDERS)) + ".",
        )

    spec = LOCAL_AI_PROVIDERS[provider_name]
    api_path = spec["path"]
    collection_key = spec["collection"]
    name_key = spec["name"]
    default_port = spec["port"]
    target_port = port if port is not None else default_port

    # Хост ограничен loopback: эндпоинт не должен превращаться в общий
    # серверный запрос по произвольному адресу.
    target_host = (host or "").strip() or "127.0.0.1"
    if target_host.lower() not in {"127.0.0.1", "localhost", "::1", "[::1]"}:
        return JSONResponse(
            status_code=400,
            content={
                "provider": provider_name,
                "models": [],
                "detail": "Список моделей запрашивается только у локального сервера (127.0.0.1).",
            },
        )

    url = "http://" + target_host + ":" + str(target_port) + api_path
    timeout = aiohttp.ClientTimeout(total=4.0)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status < 200 or response.status >= 300:
                    return JSONResponse(
                        status_code=503,
                        content={
                            "provider": provider_name,
                            "models": [],
                            "detail": (
                                f"{spec['label']} не ответил на {target_host}:{target_port}. "
                                + spec["hint"]
                            ),
                        },
                    )
                payload = await response.json(content_type=None)
    except TimeoutError:
        return JSONResponse(
            status_code=503,
            content={
                "provider": provider_name,
                "models": [],
                "detail": "Истекло время ожидания локального AI.",
            },
        )
    except (aiohttp.ClientError, ValueError, OSError):
        return JSONResponse(
            status_code=503,
            content={
                "provider": provider_name,
                "models": [],
                "detail": (
                    f"Не удалось подключиться к {spec['label']} на {target_host}:{target_port}. "
                    + spec["hint"]
                ),
            },
        )

    raw_models = payload.get(collection_key, []) if isinstance(payload, dict) else []
    if not isinstance(raw_models, list):
        raw_models = []
    models = []
    seen = set()
    for item in raw_models:
        name = item.get(name_key) if isinstance(item, dict) else None
        if not isinstance(name, str):
            continue
        normalized = name.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            models.append(normalized)

    return {"provider": provider_name, "connected": True, "models": models}


@app.get("/api/proxy/status")
async def api_proxy_status() -> dict:
    # Статус прокси кэшируется на PROXY_STATUS_TTL: проверка может длиться до
    # 20 секунд, и без кэша панель подвисала бы на каждом автообновлении.
    # Истёкший TTL принудительно перепроверяется, чтобы карточка показывала
    # актуальное состояние, а не замороженный результат первой проверки.
    checked_at = _proxy_runtime_status.get("checked_at")
    stale = checked_at is None or (time.time() - float(checked_at)) > PROXY_STATUS_TTL
    if stale:
        await refresh_proxy_runtime_status()
    return get_proxy_runtime_status()


@app.post("/api/proxy/test")
async def api_proxy_test(request: Request) -> dict:
    data = await _read_json_object(request)

    cfg = {key: str(data.get(key, "")) for key in PROXY_SETTING_KEYS}

    # Панель не знает сохранённые секреты (вместо них маска), поэтому пустое
    # или замаскированное поле означает «использовать сохранённое значение».
    stored = db.get_all_settings()
    for key in PROXY_SETTING_KEYS & SECRET_SETTING_KEYS:
        if cfg.get(key, "") in ("", SECRET_MASK):
            saved_value = str(stored.get(key, "") or "")
            if saved_value:
                cfg[key] = saved_value

    try:
        cfg = normalize_mtproto_proxy_link(cfg)
    except ValueError as exc:
        result = {
            "ok": False,
            "reachable": False,
            "state": "error",
            "message": str(exc),
            "proxy_type": "MTProto",
        }
    else:
        result = await check_proxy(cfg)
    _proxy_runtime_status.update(
        {
            "state": result.get("state", "unknown"),
            "reachable": bool(result.get("reachable")),
            "proxy_type": result.get("proxy_type") or "",
            "message": result.get("message") or "",
            "checked_at": time.time(),
        }
    )
    return result


# === Очередь постов ===
POST_STATUSES = {"draft", "scheduled", "published", "cancelled", "failed"}
POST_MEDIA_DIR = "queue_media"

#: Расширения файлов, которые разрешено прикреплять к посту. Ровно те же,
#: что Telegram принимает в постах.
ALLOWED_MEDIA_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic",
    ".mp4", ".mov", ".mkv", ".webm", ".avi",
    ".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".csv", ".json", ".xml",
})

#: Предельный размер прикрепляемого файла — как в Telegram.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _post_row_to_dict(row: dict) -> dict:
    """Готовит запись очереди к отдаче в панель."""
    try:
        media_files = json.loads(row.get("media_files") or "[]")
    except (TypeError, ValueError):
        media_files = []
    if not isinstance(media_files, list):
        media_files = []
    return {
        "id": row.get("id"),
        "source_name": row.get("source_name") or "",
        "post_id": row.get("post_id") or "",
        # Метка поста: по ней видно, какому посту принадлежит медиа.
        "media_marker": media_marker.media_marker(
            row.get("source_name") or "", row.get("post_id") or ""
        ),
        "original_text": row.get("original_text") or "",
        "publish_text": row.get("publish_text") or "",
        "media_files": media_files,
        "media_count": len(media_files),
        "media_kind": row.get("media_kind") or "",
        "category": row.get("category") or "",
        "target_chat": row.get("target_chat") or "",
        "status": row.get("status") or "draft",
        "scheduled_at": row.get("scheduled_at") or "",
        "published_at": row.get("published_at") or "",
        "last_error": row.get("last_error") or "",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
    }


@app.get("/api/posts")
async def api_list_posts(
    status: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict:
    """Список постов очереди со счётчиками по статусам."""
    wanted = (status or "").strip().lower()
    if wanted and wanted not in POST_STATUSES:
        raise HTTPException(status_code=400, detail="Неизвестный статус поста.")
    rows = db.list_queued_posts(status=wanted, limit=limit)
    counts = db.count_queued_posts()
    return {
        "posts": [_post_row_to_dict(row) for row in rows],
        "counts": {status: counts.get(status, 0) for status in sorted(POST_STATUSES)},
        "total": sum(counts.values()),
    }


@app.get("/api/posts/media-stats")
async def api_media_stats() -> dict:
    """Сводка по медиафайлам очереди: сколько постов с медиа и сколько места занято."""
    media_dir = os.path.join(BASE_DIR, POST_MEDIA_DIR)
    total_bytes = 0
    file_count = 0
    if os.path.isdir(media_dir):
        for name in os.listdir(media_dir):
            full = os.path.join(media_dir, name)
            if os.path.isfile(full):
                file_count += 1
                with contextlib.suppress(OSError):
                    total_bytes += os.path.getsize(full)
    settings = db.get_all_settings()
    retention = str(settings.get("media_retention_days", "3") or "3")
    posts_with_media, _ = db.total_media_size()
    return {
        "posts_with_media": posts_with_media,
        "files": file_count,
        "bytes": total_bytes,
        "megabytes": round(total_bytes / (1024 * 1024), 2),
        "retention_days": retention,
        "cleanup_enabled": str(settings.get("media_cleanup_enabled", "1")) == "1",
    }


def _delete_media_files(names: list[str]) -> tuple[int, int]:
    """Удаляет медиафайлы очереди. Возвращает (сколько удалено, сколько байт освобождено)."""
    media_dir = os.path.join(BASE_DIR, POST_MEDIA_DIR)
    removed = 0
    freed = 0
    for name in names:
        safe_name = os.path.basename(str(name).replace("\\", "/"))
        if not safe_name or safe_name != str(name):
            continue
        full = os.path.join(media_dir, safe_name)
        if not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
            os.remove(full)
        except OSError:
            continue
        removed += 1
        freed += size
    return removed, freed


@app.post("/api/posts/cleanup")
async def api_cleanup_posts(request: Request) -> dict:
    """Очищает медиа: по сроку хранения (retention) либо всё (scope=all)."""
    data = await _read_json_object(request)
    scope = str(data.get("scope") or "retention").strip().lower()

    settings = db.get_all_settings()
    retention = int(str(settings.get("media_retention_days", "3") or "0").strip() or 0)

    if scope == "all":
        # Полная очистка: снимаем медиа со всех постов и удаляем файлы.
        _, rows = db.total_media_size()
        names: list[str] = []
        for _post_id, media_files in rows:
            try:
                parsed = json.loads(media_files or "[]")
            except (TypeError, ValueError):
                parsed = []
            if isinstance(parsed, list):
                names.extend(str(n) for n in parsed)
        for post_id, _files in rows:
            db.clear_post_media(post_id)
        removed, freed = _delete_media_files(names)
        db.add_log("OK-513", f"Медиа очищено вручную: файлов {removed}, освобождено {freed} байт.")
        return {"status": "success", "removed": removed, "freed": freed, "scope": "all"}

    if retention <= 0:
        return {
            "status": "skipped",
            "removed": 0,
            "freed": 0,
            "scope": "retention",
            "message": "Срок хранения не задан (0 — хранить вечно).",
        }

    expired = db.published_posts_with_media(retention)
    names = []
    for post in expired:
        names.extend(post["media_files"])
        db.clear_post_media(post["id"])
    removed, freed = _delete_media_files(names)
    db.add_log(
        "OK-513",
        f"Медиа старше {retention} дн. удалено: файлов {removed}, освобождено {freed} байт.",
    )
    return {"status": "success", "removed": removed, "freed": freed, "scope": "retention"}


@app.get("/api/posts/{post_id}")
async def api_get_post(post_id: int) -> dict:
    row = db.get_queued_post(post_id)
    if not row:
        raise HTTPException(status_code=404, detail="Пост не найден.")
    return _post_row_to_dict(row)


@app.post("/api/posts/{post_id}")
async def api_update_post(post_id: int, request: Request) -> dict:
    """Правка текста, категории, канала и времени публикации."""
    data = await _read_json_object(request)
    if not db.get_queued_post(post_id):
        raise HTTPException(status_code=404, detail="Пост не найден.")

    changes: dict = {}
    if "publish_text" in data:
        changes["publish_text"] = str(data.get("publish_text") or "")
    if "category" in data:
        changes["category"] = str(data.get("category") or "").strip().lower()
    if "target_chat" in data:
        changes["target_chat"] = str(data.get("target_chat") or "").strip()
    if "scheduled_at" in data:
        raw = str(data.get("scheduled_at") or "").strip()
        if raw:
            changes["scheduled_at"] = raw
            changes["status"] = "scheduled"
        else:
            changes["scheduled_at"] = ""
            changes["status"] = "draft"
    if "status" in data:
        wanted = str(data.get("status") or "").strip().lower()
        if wanted not in POST_STATUSES:
            raise HTTPException(status_code=400, detail="Неизвестный статус поста.")
        changes["status"] = wanted
    if not changes:
        raise HTTPException(status_code=400, detail="Нет полей для сохранения.")

    if not db.update_queued_post(post_id, changes):
        raise HTTPException(status_code=500, detail="Не удалось сохранить пост.")
    row = db.get_queued_post(post_id)
    return {"status": "success", "post": _post_row_to_dict(row) if row else {}}


@app.post("/api/posts/{post_id}/cancel")
async def api_cancel_post(post_id: int) -> dict:
    """Отменяет публикацию поста."""
    if not db.get_queued_post(post_id):
        raise HTTPException(status_code=404, detail="Пост не найден.")
    db.update_queued_post(post_id, {"status": "cancelled", "scheduled_at": ""})
    row = db.get_queued_post(post_id)
    return {"status": "success", "post": _post_row_to_dict(row) if row else {}}


@app.post("/api/posts/{post_id}/delete")
async def api_delete_post(post_id: int) -> dict:
    """Удаляет пост из очереди."""
    if not db.delete_queued_post(post_id):
        raise HTTPException(status_code=404, detail="Пост не найден.")
    return {"status": "success"}


@app.get("/api/posts/{post_id}/media/{index}")
async def api_get_post_media(post_id: int, index: int) -> FileResponse:
    """Отдаёт медиафайл поста из очереди."""
    row = db.get_queued_post(post_id)
    if not row:
        raise HTTPException(status_code=404, detail="Пост не найден.")
    try:
        files = json.loads(row.get("media_files") or "[]")
    except (TypeError, ValueError):
        files = []
    if not isinstance(files, list) or index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="Медиафайл не найден.")
    name = str(files[index] or "")
    # Имя файла приходит из базы, но путь собирается строго внутри каталога
    # очереди: выход за пределы каталога невозможен.
    safe_name = os.path.basename(name.replace("\\", "/"))
    if not safe_name or safe_name != name:
        raise HTTPException(status_code=400, detail="Некорректное имя файла.")
    full = os.path.join(BASE_DIR, POST_MEDIA_DIR, safe_name)
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="Медиафайл не найден.")
    return FileResponse(full)


@app.post("/api/posts/{post_id}/media")
async def api_add_post_media(post_id: int, request: Request) -> dict:
    """Прикрепляет файл к посту в очереди.

    Файл приходит телом запроса, имя — параметром ``name``. Так не нужна
    дополнительная библиотека для разбора multipart-форм.
    """
    row = db.get_queued_post(post_id)
    if not row:
        raise HTTPException(status_code=404, detail="Пост не найден.")
    status = str(row.get("status") or "")
    if status in {"published", "cancelled"}:
        raise HTTPException(status_code=409, detail="К этому посту уже нельзя прикреплять медиа.")

    raw_name = str(request.query_params.get("name") or "").strip()
    safe_name = os.path.basename(raw_name.replace("\\", "/"))
    if not safe_name or safe_name != raw_name:
        raise HTTPException(status_code=400, detail="Некорректное имя файла.")
    extension = os.path.splitext(safe_name)[1].lower()
    if extension not in ALLOWED_MEDIA_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Такой тип файла не поддерживается: {extension or 'без расширения'}.",
        )

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Файл пуст.")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ — Telegram столько не примет.",
        )

    try:
        files = json.loads(row.get("media_files") or "[]")
    except (TypeError, ValueError):
        files = []
    if not isinstance(files, list):
        files = []

    media_dir = os.path.join(BASE_DIR, POST_MEDIA_DIR)
    os.makedirs(media_dir, exist_ok=True)
    marker = media_marker.media_marker(
        row.get("source_name") or "", row.get("post_id") or ""
    )
    file_name = media_marker.media_file_name(
        marker, int(time.time() * 1000), len(files), extension
    )
    try:
        with open(os.path.join(media_dir, file_name), "wb") as handle:
            handle.write(body)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Не удалось сохранить файл: {exc}") from exc

    files.append(file_name)
    db.update_queued_post(
        post_id,
        {"media_files": json.dumps(files, ensure_ascii=False)},
    )
    db.add_log("OK-514", f"К посту {post_id} прикреплён файл {safe_name}.")
    updated = db.get_queued_post(post_id)
    return {
        "status": "success",
        "file": safe_name,
        "post": _post_row_to_dict(updated) if updated else {},
    }


@app.delete("/api/posts/{post_id}/media/{index}")
async def api_delete_post_media(post_id: int, index: int) -> dict:
    """Убирает один медиафайл из поста."""
    row = db.get_queued_post(post_id)
    if not row:
        raise HTTPException(status_code=404, detail="Пост не найден.")
    status = str(row.get("status") or "")
    if status in {"published", "cancelled"}:
        raise HTTPException(status_code=409, detail="Медиа опубликованного поста менять нельзя.")

    try:
        files = json.loads(row.get("media_files") or "[]")
    except (TypeError, ValueError):
        files = []
    if not isinstance(files, list) or index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="Медиафайл не найден.")

    removed_name = str(files.pop(index) or "")
    changes: dict = {"media_files": json.dumps(files, ensure_ascii=False)}
    if not files:
        changes["media_kind"] = ""
    db.update_queued_post(post_id, changes)
    _delete_media_files([removed_name])
    db.add_log("OK-514", f"У поста {post_id} удалён медиафайл {removed_name}.")
    updated = db.get_queued_post(post_id)
    return {
        "status": "success",
        "removed": removed_name,
        "post": _post_row_to_dict(updated) if updated else {},
    }


@app.post("/api/posts/{post_id}/publish")
async def api_publish_post(post_id: int) -> dict:
    """Публикует пост из очереди прямо сейчас."""
    row = db.get_queued_post(post_id)
    if not row:
        raise HTTPException(status_code=404, detail="Пост не найден.")

    import main as parser_module

    status = str(row.get("status") or "")
    if status == "published":
        raise HTTPException(status_code=409, detail="Пост уже опубликован.")
    if status == "cancelled":
        raise HTTPException(status_code=409, detail="Публикация поста отменена.")

    settings = db.get_all_settings()
    token = (settings.get("bot_token") or "").strip()
    if not token:
        raise HTTPException(
            status_code=400,
            detail="Не задан Bot Token — публиковать нечем. Заполните раздел «Подключение».",
        )

    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        ok, message = await parser_module.publish_queued_post(bot, row)
    finally:
        with contextlib.suppress(Exception):
            await bot.session.close()

    if not ok:
        db.update_queued_post(post_id, {"status": "failed", "last_error": message})
        raise HTTPException(status_code=502, detail=f"Не удалось опубликовать: {message}")

    db.update_queued_post(
        post_id,
        {
            "status": "published",
            "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scheduled_at": "",
            "last_error": "",
        },
    )
    db.add_log("OK-512", f"Пост {post_id} опубликован из очереди в {row.get('target_chat')}.")
    updated = db.get_queued_post(post_id)
    return {"status": "success", "post": _post_row_to_dict(updated) if updated else {}}


@app.post("/api/posts/{post_id}/clear-media")
async def api_clear_post_media(post_id: int) -> dict:
    """Убирает медиа у конкретного поста и удаляет файлы."""
    if not db.get_queued_post(post_id):
        raise HTTPException(status_code=404, detail="Пост не найден.")
    names = db.clear_post_media(post_id)
    removed, freed = _delete_media_files(names)
    return {"status": "success", "removed": removed, "freed": freed}


# === Серверное время ===
@app.get("/api/time")
async def api_server_time() -> dict[str, object]:
    now = datetime.now()
    return {
        "iso": now.strftime("%Y-%m-%d %H:%M:%S"),
        "display": format_display_timestamp(now.strftime("%Y-%m-%d %H:%M:%S")),
        "epoch_ms": int(now.timestamp() * 1000),
    }


# === Логи ===
# Bound offset scans so an arbitrary page parameter cannot force huge SQLite work.
MAX_LOGS_OFFSET = 1_000_000


@app.get("/api/logs")
async def api_get_logs(
    limit: int = LOGS_PAGE_SIZE,
    page: int = 1,
    module: str | None = Query(default=None),
) -> list[dict]:
    limit = max(limit, 1)
    limit = min(limit, 200)
    page = max(page, 1)
    offset = (page - 1) * limit
    if offset > MAX_LOGS_OFFSET:
        return []
    rows = db.get_logs(limit=limit, offset=offset)
    return [
        {
            "timestamp": ts,
            "timestamp_display": format_display_timestamp(ts),
            "type": t,
            "message": m,
        }
        for (ts, t, m) in rows
    ]


@app.post("/api/logs/clear")
async def api_clear_logs(request: Request) -> dict:
    """Удаляет записи журнала.

    Требует явного подтверждения (`confirm: true`), чтобы случайный запрос
    не стёр историю. Тексты удалённых записей в ответе не возвращаются.
    """
    data = await _read_json_object(request)
    if not data.get("confirm"):
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "msg": "Очистка журнала требует подтверждения (confirm=true).",
            },
        )
    try:
        removed = db.clear_logs()
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "msg": f"Не удалось очистить журнал: {type(exc).__name__}.",
            },
        )
    return {"status": "success", "removed": int(removed)}


# === Статистика модуля постов ===
def _build_posts_stats() -> dict:
    today_iso = db.sqlite_datetime("start of day")
    h24_iso = db.sqlite_datetime("-24 hours")
    h1_iso = db.sqlite_datetime("-1 hour")

    published_total = db.count_total("published_posts")
    skipped_total = db.count_total("skipped_posts")

    published_today = db.count_published_since(today_iso)
    published_24h = db.count_published_since(h24_iso)
    skipped_today = db.count_skipped_since(today_iso)
    skipped_24h = db.count_skipped_since(h24_iso)

    skipped_by_reason = db.top_skipped_reasons(h24_iso)

    successes_24h = db.count_logs_by_type(("OK-", "SUCCESS"), h24_iso)
    errors_24h = db.count_logs_by_type(("ERR-",), h24_iso)

    last_ts, last_source = db.last_published()
    last_published_at = last_ts
    last_published_source = last_source

    last_err = db.last_log_by_prefix("ERR-")
    last_error_code = last_err[1] if last_err else None
    last_error_msg = last_err[2] if last_err else None

    top_sources = [
        {"source": s, "count": c}
        for s, c in db.top_published_sources(h24_iso, limit=5)
    ]

    ai_status = "ok"
    recent_ai_err = db.last_log_by_prefix("ERR-4")
    if recent_ai_err and recent_ai_err[0] and recent_ai_err[0] >= h1_iso:
        ai_status = "error"

    settings = db.get_all_settings()
    ai_mode = (settings.get("ai_mode") or "LOCAL").upper()
    ai_model = (
        settings.get("local_model")
        if ai_mode == "LOCAL"
        else settings.get("remote_model")
    ) or ""

    sources_raw = (settings.get("sources") or "").strip()
    sources_list = [s for s in sources_raw.split("\n") if s.strip()] if sources_raw else []
    sources_count = len(sources_list)
    active_sources_24h = db.count_active_published_sources_since(h24_iso)

    # Маршруты по тегам: канал → список тегов. Старые настройки
    # «категория → канал» тоже считаются, чтобы статистика не обнулялась.
    try:
        routes = routing.parse_routes(settings.get("tag_routes") or "")
        if not routes:
            routes = routing.parse_routes(settings.get("topic_channels") or "")
    except (TypeError, ValueError):
        routes = {}
    topics_count = len(routes)
    topics_with_target = sum(1 for tags in routes.values() if tags)

    return {
        "bot_running": bot_running.is_set(),
        "enabled": True,
        "sources_count": sources_count,
        "active_sources_24h": active_sources_24h,
        "topics_count": topics_count,
        "topics_with_target": topics_with_target,
        "ai_mode": ai_mode,
        "ai_model": ai_model,
        "ai_status": ai_status,
        "published_total": int(published_total),
        "published_today": int(published_today),
        "published_24h": int(published_24h),
        "skipped_total": int(skipped_total),
        "skipped_today": int(skipped_today),
        "skipped_24h": int(skipped_24h),
        "skipped_by_reason": skipped_by_reason,
        "successes_24h": int(successes_24h),
        "errors_24h": int(errors_24h),
        "last_published_at": last_published_at,
        "last_published_source": last_published_source,
        "last_error_code": last_error_code,
        "last_error_msg": last_error_msg,
        "top_sources_24h": top_sources,
        "proxy_status": get_proxy_runtime_status(),
    }


@app.get("/api/stats/posts")
async def api_stats_posts() -> dict:
    try:
        return _build_posts_stats()
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )


@app.on_event("startup")
async def _api_startup_tasks() -> None:
    from startup import prepare_runtime_directories
    prepare_runtime_directories()


if __name__ == "__main__":
    import uvicorn

    # Зависимости проверяются только в точке входа. Вызов на этапе импорта
    # мог перезапустить процесс (os.execv) внутри pytest или другого
    # приложения, которое импортирует Site.
    check_and_install_dependencies()

    from config import API_HOST, API_PORT
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level=API_LOG_LEVEL)
