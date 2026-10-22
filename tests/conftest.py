"""Общая настройка тестов Content Factory.

Тесты ходят по панели как «свой» клиент: имя хоста в служебном TestClient —
``testserver``, а браузерных заголовков Origin/Sec-Fetch-Site у него нет.
По умолчанию такие запросы считаются доверенными.

Тесты, которые проверяют саму защиту, помечаются маркером
``real_security`` — для них проверки остаются настоящими.
"""
from __future__ import annotations

import pytest

import api_security

_original_allowed_hosts = api_security.allowed_hosts


@pytest.fixture(autouse=True)
def _trusted_test_client(monkeypatch, request):
    """Разрешает служебный хост TestClient и доверяет его запросам."""
    def patched_allowed_hosts(api_host: str) -> set[str]:
        hosts = _original_allowed_hosts(api_host)
        hosts.add("testserver")
        return hosts

    monkeypatch.setattr(api_security, "allowed_hosts", patched_allowed_hosts)

    if request.node.get_closest_marker("real_security") is None:
        monkeypatch.setattr(
            api_security,
            "is_trusted_write",
            lambda request, host_header: True,
        )
