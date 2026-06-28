# REFACTORED: Only Core Bot (Posts) module left. License, PF, and Reviews completely removed.
"""
FastAPI-приложение: HTML-админка, JSON API для управления основным ботом публикаций.
"""
from __future__ import annotations

import os
import re
import time
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from deps import check_and_install_dependencies
check_and_install_dependencies()

from proxy_check import check_proxy, resolve_proxy_type
from config import (
    ADMIN_HTML_PATH,
    APP_VERSION,
    BASE_DIR,
    BUNDLE_DIR,
    API_LOG_LEVEL,
    LOGS_PAGE_SIZE,
    MAX_PROXY_PORT,
    MIN_PROXY_PORT,
    MODULE_POSTS,
    POLL_DELAY_MAX,
    POLL_DELAY_MIN,
    SETTING_KEYS,
)
from database import format_display_timestamp, get_db
from state import bot_running

db = get_db()
app = FastAPI(title="API Panel Marej")

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
    if not raw.isdigit():
        raise SettingsValidationError(field=field, msg=f"{field} должен быть целым числом")
    value = int(raw)
    if min_value is not None and value < min_value:
        raise SettingsValidationError(field=field, msg=f"{field} должен быть >= {min_value}")
    if max_value is not None and value > max_value:
        raise SettingsValidationError(field=field, msg=f"{field} должен быть <= {max_value}")
    return str(value)


def _normalize_validated_setting(key: str, value) -> str:
    """Валидирует оставшиеся глобальные ключи настроек."""
    raw = "" if value is None else str(value).strip()

    if key == "poll_delay":
        return _normalize_int_range(
            "poll_delay",
            raw,
            min_value=POLL_DELAY_MIN,
            max_value=POLL_DELAY_MAX,
        )

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


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    if not os.path.exists(ADMIN_HTML_PATH):
        return HTMLResponse(
            content="<h1>Ошибка: admin.html не найден рядом с исполняемым файлом</h1>",
            status_code=500,
        )
    with open(ADMIN_HTML_PATH, "r", encoding="utf-8") as f:
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
        "licensed": True,
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
@app.get("/api/settings")
async def api_get_settings(module: Optional[str] = Query(default=None)) -> dict:
    return db.get_all_settings()


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


def _proxy_cfg_from_settings(settings: Optional[dict] = None) -> dict:
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


async def refresh_proxy_runtime_status(settings: Optional[dict] = None) -> dict:
    cfg = _proxy_cfg_from_settings(settings)
    use_proxy = str(cfg.get("use_proxy", "0")).strip() in ("1", "true", "True")
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
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Ожидается JSON-объект")

    rejected: list[str] = []
    accepted: dict[str, str] = {}
    for key, value in data.items():
        if key not in SETTING_KEYS:
            rejected.append(key)
            continue

        try:
            accepted[key] = _normalize_validated_setting(key, value)
        except SettingsValidationError as e:
            return JSONResponse(
                status_code=422,
                content={"status": "error", "field": e.field, "msg": e.msg},
            )

    for key, normalized_value in accepted.items():
        db.set_setting(key, normalized_value)

    saved = len(accepted)
    if accepted.keys() & PROXY_SETTING_KEYS:
        background_tasks.add_task(refresh_proxy_runtime_status)
    if rejected:
        return {"status": "partial", "saved": saved, "rejected_keys": rejected}
    return {"status": "success", "saved": saved}


@app.get("/api/proxy/status")
async def api_proxy_status() -> dict:
    if _proxy_runtime_status.get("checked_at") is None:
        await refresh_proxy_runtime_status()
    return get_proxy_runtime_status()


@app.post("/api/proxy/test")
async def api_proxy_test(request: Request) -> dict:
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Ожидается JSON-объект")

    cfg = {key: str(data.get(key, "")) for key in PROXY_SETTING_KEYS}
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
@app.get("/api/logs")
async def api_get_logs(
    limit: int = LOGS_PAGE_SIZE,
    page: int = 1,
    module: Optional[str] = Query(default=None),
) -> list[dict]:
    if limit > 200:
        limit = 200
    if page < 1:
        page = 1
    offset = (page - 1) * limit
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

    successes_24h = db.count_logs_by_type(("OK", "SUCCESS", "SYS-"), h24_iso)
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
    active_sources_24h = sum(1 for s in top_sources if s["count"] > 0)

    try:
        import json as _json
        topics = _json.loads(settings.get("topic_channels") or "{}")
    except Exception:
        topics = {}
    topics_count = len(topics)
    topics_with_target = sum(1 for v in topics.values() if v)

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
        return {
            "error": str(e),
            "bot_running": False,
            "published_total": 0,
            "skipped_total": 0,
        }


@app.on_event("startup")
async def _api_startup_tasks() -> None:
    from startup import prepare_runtime_directories
    prepare_runtime_directories()


from integrations.register import register_integrations
register_integrations(app)

if __name__ == "__main__":
    import uvicorn
    from config import API_HOST, API_PORT
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level=API_LOG_LEVEL)