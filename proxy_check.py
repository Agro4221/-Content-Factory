"""Проверка доступности прокси-соединения (HTTP / SOCKS / MTProto)."""
from __future__ import annotations

import asyncio
import base64
import re
from typing import Any, Optional, Type
from urllib.parse import quote

import aiohttp

from config import MAX_PROXY_PORT, MIN_PROXY_PORT

try:
    from aiohttp_socks import ProxyConnector

    _HAS_SOCKS = True
except ImportError:
    _HAS_SOCKS = False


def _validate_port(port_str: str) -> Optional[int]:
    if not port_str or not str(port_str).isdigit():
        return None
    port = int(port_str)
    return port if MIN_PROXY_PORT <= port <= MAX_PROXY_PORT else None


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def resolve_proxy_type(cfg: dict) -> str:
    if (cfg.get("mtproto_key") or "").strip():
        return "mtproto"
    return (cfg.get("proxy_type") or "http").strip().lower()


def _friendly_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "proxy closed the connection after sending initial payload" in msg:
        return "Прокси отклонил секрет или тип соединения"
    if "incompletereaderror" in type(exc).__name__.lower() or "bytes read on a total of" in msg:
        return "Прокси ответил, но MTProto-туннель не установился. Проверьте секретный ключ"
    if "unexpected keyword argument 'secret'" in msg:
        return "Внутренняя ошибка MTProto-клиента (неверный формат прокси)"
    if any(
        token in msg
        for token in (
            "refused",
            "timeout",
            "unreachable",
            "not found",
            "getaddrinfo",
            "nodename",
            "name or service",
            "cannot connect",
            "no route",
        )
    ):
        return "Сервер не найден или недоступен"
    return str(exc) or "Сервер не найден или недоступен"


async def _test_http(
    host: str,
    port: int,
    user: str,
    password: str,
    timeout: float,
) -> tuple[bool, str]:
    auth = ""
    if user or password:
        auth = f"{quote(str(user), safe='')}:{quote(str(password), safe='')}@"
    proxy_url = f"http://{auth}{host}:{port}"
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get("https://api.telegram.org", proxy=proxy_url) as resp:
                if resp.status < 500:
                    return True, "Соединение установлено"
                return False, f"Прокси ответил с кодом {resp.status}"
    except asyncio.TimeoutError as exc:
        raise exc
    except Exception as exc:
        return False, _friendly_error(exc)


async def _test_socks(
    proxy_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    timeout: float,
) -> tuple[bool, str]:
    if not _HAS_SOCKS:
        return False, "Для SOCKS нужен пакет aiohttp-socks"

    scheme = "socks5" if proxy_type == "socks5" else "socks4"
    auth = ""
    if user or password:
        auth = f"{quote(str(user), safe='')}:{quote(str(password), safe='')}@"
    proxy_url = f"{scheme}://{auth}{host}:{port}"
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    connector = ProxyConnector.from_url(proxy_url)
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=client_timeout) as session:
            async with session.get("https://api.telegram.org") as resp:
                if resp.status < 500:
                    return True, "Соединение установлено"
                return False, f"Прокси ответил с кодом {resp.status}"
    finally:
        await connector.close()


def decode_mtproto_secret_full(secret: str) -> bytes:
    """Декодирует MTProto-секрет целиком (включая домен fake TLS)."""
    raw = (secret or "").strip()
    if not raw:
        raise ValueError("Укажите секретный ключ MTProto")

    if re.fullmatch(r"[0-9a-fA-F]+", raw):
        data = bytes.fromhex(raw)
    else:
        padded = raw + "=" * (-len(raw) % 4)
        data = base64.b64decode(padded.encode())

    if len(data) < 16:
        raise ValueError("Секретный ключ MTProto слишком короткий")
    return data


def normalize_mtproto_secret(secret: str) -> bytes:
    """Декодирует MTProto-секрет (hex или base64) в 16 байт для Telethon."""
    return decode_mtproto_secret_full(secret)[:16]


def is_faketls_mtproto_secret(secret: str) -> bool:
    """Fake TLS (ee + домен): в Telegram работает, в Telethon — только через TelethonFakeTLS."""
    try:
        data = decode_mtproto_secret_full(secret)
    except ValueError:
        return False
    return data[0] == 0xEE and len(data) > 16


def telethon_faketls_proxy_secret(secret: str) -> str:
    """Секрет для TelethonFakeTLS: без префикса ee/7, домен сохраняется."""
    raw = (secret or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]+", raw) and raw[:2].lower() in ("ee", "dd"):
        return raw[2:]
    if not re.fullmatch(r"[0-9a-fA-F]+", raw) and raw.startswith("7"):
        return raw[1:]
    return raw


def _faketls_connection_class():
    try:
        import TelethonFakeTLS
    except ImportError as exc:
        raise ImportError(
            "Для MTProto Fake TLS установите пакет TelethonFakeTLS"
        ) from exc
    return TelethonFakeTLS.ConnectionTcpMTProxyFakeTLS


def telethon_mtproto_secret(secret: str) -> str:
    """Канонический base64-секрет без паддинга — безопасен для Telethon."""
    return base64.b64encode(normalize_mtproto_secret(secret)).decode().rstrip("=")


def mtproto_connection_classes(secret: str) -> list[Type]:
    """Подбирает классы MTProxy-соединения Telethon по типу секрета."""
    from telethon.network import (
        ConnectionTcpMTProxyAbridged,
        ConnectionTcpMTProxyIntermediate,
        ConnectionTcpMTProxyRandomizedIntermediate,
    )

    first = normalize_mtproto_secret(secret)[0]
    if first == 0xDD:
        primary = ConnectionTcpMTProxyRandomizedIntermediate
        fallbacks = (
            ConnectionTcpMTProxyIntermediate,
            ConnectionTcpMTProxyAbridged,
        )
    elif first == 0xEE:
        primary = ConnectionTcpMTProxyIntermediate
        fallbacks = (
            ConnectionTcpMTProxyRandomizedIntermediate,
            ConnectionTcpMTProxyAbridged,
        )
    else:
        primary = ConnectionTcpMTProxyIntermediate
        fallbacks = (
            ConnectionTcpMTProxyRandomizedIntermediate,
            ConnectionTcpMTProxyAbridged,
        )

    ordered: list[Type] = [primary]
    for cls in fallbacks:
        if cls not in ordered:
            ordered.append(cls)
    return ordered


def build_mtproto_client_kwargs(host: str, port: int, secret: str) -> dict[str, Any]:
    """Параметры TelegramClient для MTProto-прокси (tuple + connection class)."""
    if is_faketls_mtproto_secret(secret):
        return {
            "connection": _faketls_connection_class(),
            "proxy": (host.strip(), int(port), telethon_faketls_proxy_secret(secret)),
        }

    telethon_secret = telethon_mtproto_secret(secret)
    connection = mtproto_connection_classes(secret)[0]
    return {
        "connection": connection,
        "proxy": (host.strip(), int(port), telethon_secret),
    }


def _mtproto_connection_attempts(secret: str) -> list[tuple[Type, str]]:
    """Пары (connection class, proxy secret) для проверки MTProto."""
    if is_faketls_mtproto_secret(secret):
        return [
            (_faketls_connection_class(), telethon_faketls_proxy_secret(secret)),
        ]

    telethon_secret = telethon_mtproto_secret(secret)
    return [(cls, telethon_secret) for cls in mtproto_connection_classes(secret)]


async def _tcp_reachable(host: str, port: int, timeout: float) -> tuple[bool, str]:
    """Быстрая проверка, что хост:порт прокси доступен с этой машины."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True, ""
    except asyncio.TimeoutError:
        return False, "Сервер прокси не отвечает (таймаут TCP)"
    except OSError as exc:
        return False, _friendly_error(exc)


async def _test_mtproto(host: str, port: int, secret: str, timeout: float) -> tuple[bool, str]:
    if not secret:
        return False, "Укажите секретный ключ MTProto"

    tcp_ok, tcp_msg = await _tcp_reachable(host, port, min(timeout, 5.0))
    if not tcp_ok:
        return False, tcp_msg or "Сервер прокси недоступен с этого компьютера"

    from telethon import TelegramClient
    from telethon.sessions import MemorySession

    try:
        attempts = _mtproto_connection_attempts(secret)
    except ValueError as exc:
        return False, str(exc)
    except ImportError as exc:
        return False, str(exc)

    last_message = "Сервер не найден или недоступен"
    for connection_cls, proxy_secret in attempts:
        client = TelegramClient(
            MemorySession(),
            api_id=2040,
            api_hash="b18441a9ff583e0a916148636be8dd2",
            connection=connection_cls,
            proxy=(host, port, proxy_secret),
            connection_retries=0,
        )
        try:
            await asyncio.wait_for(client.connect(), timeout=timeout)
            if client.is_connected():
                return True, "Соединение установлено"
        except asyncio.TimeoutError:
            last_message = "Таймаут: прокси не ответил вовремя (для Fake TLS может потребоваться до 20 с)"
        except Exception as exc:
            msg = str(exc)
            if "connection to telegram failed" in msg.lower():
                last_message = (
                    "Прокси принял соединение, но Telegram недоступен через этот прокси. "
                    "Проверьте секрет и что прокси активен"
                )
            elif "Proxy closed the connection after sending initial payload" in msg:
                last_message = "Прокси отклонил секрет или тип соединения (нужен Fake TLS / TelethonFakeTLS)"
            else:
                last_message = _friendly_error(exc)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    return False, last_message


async def check_proxy(cfg: dict, timeout: float = 20.0) -> dict[str, Any]:
    """Проверяет доступность прокси и возвращает статус для UI."""
    use_proxy = _truthy(cfg.get("use_proxy"))
    host = (cfg.get("proxy_host") or "").strip()

    if not use_proxy:
        return {
            "ok": True,
            "reachable": False,
            "state": "disabled",
            "message": "Прокси выключен",
            "proxy_type": "",
        }

    if not host:
        return {
            "ok": False,
            "reachable": False,
            "state": "no_host",
            "message": "Укажите хост прокси",
            "proxy_type": "",
        }

    port = _validate_port(str(cfg.get("proxy_port") or ""))
    if port is None:
        return {
            "ok": False,
            "reachable": False,
            "state": "error",
            "message": "Некорректный порт",
            "proxy_type": resolve_proxy_type(cfg).upper(),
        }

    proxy_type = resolve_proxy_type(cfg)
    try:
        if proxy_type == "mtproto":
            ok, message = await _test_mtproto(
                host,
                port,
                (cfg.get("mtproto_key") or "").strip(),
                timeout,
            )
        elif proxy_type == "http":
            ok, message = await _test_http(
                host,
                port,
                cfg.get("proxy_user") or "",
                cfg.get("proxy_pass") or "",
                timeout,
            )
        elif proxy_type in ("socks5", "socks4"):
            ok, message = await _test_socks(
                proxy_type,
                host,
                port,
                cfg.get("proxy_user") or "",
                cfg.get("proxy_pass") or "",
                timeout,
            )
        else:
            ok, message = await _test_http(
                host,
                port,
                cfg.get("proxy_user") or "",
                cfg.get("proxy_pass") or "",
                timeout,
            )
    except asyncio.TimeoutError:
        ok, message = False, "Таймаут: сервер не отвечает"
    except Exception as exc:
        ok, message = False, _friendly_error(exc)

    display_type = "MTProto" if proxy_type == "mtproto" else proxy_type.upper()
    return {
        "ok": ok,
        "reachable": ok,
        "state": "active" if ok else "error",
        "message": message,
        "proxy_type": display_type,
    }
