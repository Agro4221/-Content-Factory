"""Соединение с Telegram живёт между кругами обхода.

Раньше клиент создавался заново каждый круг: файл сессии переписывался, а
Telegram получал лишние переподключения (риск флуд-контроля). Теперь
соединение пересоздаётся только когда сменились настройки или оно
отвалилось.
"""
from __future__ import annotations

import asyncio

import main


def _cfg(**overrides) -> dict:
    cfg = {
        "bot_token": "123:ABC",
        "api_id": "111",
        "api_hash": "hash",
        "use_proxy": False,
        "proxy_type": "",
        "proxy_host": "",
        "proxy_port": "",
        "proxy_user": "",
        "proxy_pass": "",
        "mtproto_key": "",
    }
    cfg.update(overrides)
    return cfg


def test_signature_is_stable_for_the_same_settings() -> None:
    assert main._connection_signature(_cfg()) == main._connection_signature(_cfg())


def test_signature_changes_when_token_changes() -> None:
    assert main._connection_signature(_cfg()) != main._connection_signature(
        _cfg(bot_token="456:DEF")
    )


def test_signature_changes_when_proxy_changes() -> None:
    base = main._connection_signature(_cfg())
    assert base != main._connection_signature(_cfg(use_proxy=True, proxy_type="http"))
    assert base != main._connection_signature(_cfg(proxy_host="proxy.example"))
    assert base != main._connection_signature(_cfg(proxy_port="1080"))


def test_signature_does_not_change_for_unrelated_settings() -> None:
    """Пауза между кругами или список источников не требуют переподключения."""
    base = main._connection_signature(_cfg())
    assert base == main._connection_signature(_cfg(poll_delay=120, sources=["@a"]))


class FakeClient:
    def __init__(self, connected: bool) -> None:
        self._connected = connected

    def is_connected(self) -> bool:
        return self._connected


def test_connected_client_is_recognised() -> None:
    assert main._client_is_connected(FakeClient(True)) is True
    assert main._client_is_connected(FakeClient(False)) is False
    assert main._client_is_connected(None) is False


def test_client_without_is_connected_uses_legacy_flag() -> None:
    class LegacyClient:
        _connected = True

    assert main._client_is_connected(LegacyClient()) is True


def test_close_clients_closes_everything() -> None:
    """Закрытие не должно падать, даже если клиент уже отключён."""
    class Session:
        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class Bot:
        def __init__(self) -> None:
            self.session = Session()

    class Proxy:
        def __init__(self) -> None:
            self.bot_session = None

    bot = Bot()
    asyncio.run(main._close_clients(bot, None, None, Proxy()))
    assert bot.session.closed is True


def test_close_clients_survives_broken_resource() -> None:
    class BrokenSession:
        async def close(self) -> None:
            raise RuntimeError("уже закрыт")

    class Bot:
        def __init__(self) -> None:
            self.session = BrokenSession()

    class Proxy:
        bot_session = None

    # Ошибка закрытия не должна подниматься наружу.
    asyncio.run(main._close_clients(Bot(), None, None, Proxy()))
