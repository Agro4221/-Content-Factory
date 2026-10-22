"""Прокси должен покрывать весь внешний трафик, а не только Telegram.

Раньше запросы к ИИ всегда уходили напрямую, мимо включённого прокси.
Для пользователя из России это значило: облачный ИИ (OpenRouter) оставался
медленным или недоступным, хотя в панели прокси был включён.

MTProxy — исключение: он пропускает только Telegram-трафик, обычный HTTPS
через него не идёт. Это ограничение протокола, а не ошибка.
"""
from __future__ import annotations

import asyncio

import pytest

import main


def _cfg(**overrides) -> dict:
    cfg = {
        "use_proxy": True,
        "proxy_type": "http",
        "proxy_host": "proxy.example",
        "proxy_port": "8080",
        "proxy_user": "",
        "proxy_pass": "",
        "mtproto_key": "",
    }
    cfg.update(overrides)
    return cfg


def test_http_proxy_covers_ai_traffic() -> None:
    setup = main._setup_proxy(_cfg())
    assert setup.ai_proxy_url == "http://proxy.example:8080"
    assert setup.ai_proxy_kind == "http"
    assert setup.covers_all_traffic is True


def test_socks_proxy_covers_ai_traffic(monkeypatch) -> None:
    monkeypatch.setattr(main, "_HAS_SOCKS", True)
    setup = main._setup_proxy(_cfg(proxy_type="socks5", proxy_port="1080"))
    assert setup.ai_proxy_url == "socks5://proxy.example:1080"
    assert setup.ai_proxy_kind == "socks"
    assert setup.covers_all_traffic is True


def test_mtproto_does_not_pretend_to_cover_ai_traffic() -> None:
    """MTProxy не умеет HTTPS — приложение обязано это понимать."""
    setup = main._setup_proxy(
        _cfg(
            proxy_type="mtproto",
            mtproto_key="dd" + "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
        )
    )
    assert setup.ai_proxy_url is None
    assert setup.ai_proxy_kind is None
    assert setup.covers_all_traffic is False
    # Читать каналы через MTProto по-прежнему можно.
    assert setup.mtproto_kwargs is not None


def test_ai_session_uses_http_proxy() -> None:
    setup = main._setup_proxy(_cfg())

    async def scenario() -> None:
        session = main._make_ai_session(setup, timeout=30)
        try:
            assert session.timeout.total == 30
            # aiohttp хранит адрес прокси в _default_proxy.
            assert session._default_proxy is not None
            assert "proxy.example" in str(session._default_proxy)
        finally:
            await session.close()

    asyncio.run(scenario())


def test_ai_session_without_proxy_goes_direct() -> None:
    setup = main.ProxySetup()

    async def scenario() -> None:
        session = main._make_ai_session(setup, timeout=15)
        try:
            assert session._default_proxy is None
        finally:
            await session.close()

    asyncio.run(scenario())


def test_ai_session_raises_instead_of_silently_going_direct() -> None:
    """Если прокси включён, но пакет для SOCKS отсутствует — падаем явно.

    Молчаливый переход на прямое подключение означал бы, что трафик ушёл
    мимо прокси, о чём пользователь не знает.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr(main, "_HAS_SOCKS", False)
    try:
        setup = main.ProxySetup(
            ai_proxy_url="socks5://proxy.example:1080",
            ai_proxy_kind="socks",
        )
        with pytest.raises(main.ProxyConfigurationError):
            main._make_ai_session(setup, timeout=30)
    finally:
        monkey.undo()
