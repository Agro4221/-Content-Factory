"""Общие фикстуры тестов Content Factory.

FastAPI TestClient использует хост ``testserver`` и не добавляет браузерные
заголовки ``Origin`` и ``Sec-Fetch-Site``. Для обычных тестов эти запросы
считаются доверенными. Тесты безопасности помечаются маркером
``real_security``; для них фикстура не подменяет проверку доверенности write-запросов.
"""
from __future__ import annotations

import pytest

import api_security

_original_allowed_hosts = api_security.allowed_hosts


@pytest.fixture(autouse=True)
def _trusted_test_client(monkeypatch, request):
    """Настраивает обработку служебного хоста TestClient в тестах."""
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
