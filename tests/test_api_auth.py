"""Регрессионные тесты аутентификации админ-панели и безопасности привязки.

Панель по умолчанию доступна только с localhost. Если её выставляют в сеть,
учётные данные обязательны — и это проверяется ДО старта сервера, чтобы
пользователь увидел причину в терминале, а не страницу с ошибкой в браузере.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import Site


@pytest.fixture
def client() -> TestClient:
    with TestClient(Site.app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_localhost_panel_remains_usable_without_credentials(monkeypatch, client: TestClient) -> None:
    """Без учётных данных панель работает — но только на loopback."""
    monkeypatch.setattr(Site, "ADMIN_USERNAME", "")
    monkeypatch.setattr(Site, "ADMIN_PASSWORD", "")

    response = client.get("/api/bot/status")

    assert response.status_code == 200


def test_basic_auth_protects_admin_html_and_api(monkeypatch, client: TestClient) -> None:
    """С заданными учётными данными панель и API требуют авторизации."""
    monkeypatch.setattr(Site, "ADMIN_USERNAME", "panel-admin")
    monkeypatch.setattr(Site, "ADMIN_PASSWORD", "a-long-test-password")

    for path in ("/", "/api/bot/status", "/api/settings"):
        unauthenticated = client.get(path)
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"].startswith("Basic ")

    wrong_password = client.get("/api/bot/status", auth=("panel-admin", "wrong"))
    assert wrong_password.status_code == 401

    authenticated = client.get("/api/bot/status", auth=("panel-admin", "a-long-test-password"))
    assert authenticated.status_code == 200


def test_basic_auth_parser_rejects_malformed_headers() -> None:
    """Битый заголовок не должен приводить к успешной авторизации."""
    assert not Site._basic_auth_matches(None, "admin", "password")
    assert not Site._basic_auth_matches("", "admin", "password")
    assert not Site._basic_auth_matches("Bearer token", "admin", "password")
    assert not Site._basic_auth_matches("Basic not-base64!", "admin", "password")
    # base64 без разделителя «логин:пароль»
    assert not Site._basic_auth_matches("Basic YWRtaW4=", "admin", "password")


def test_network_bind_requires_credentials() -> None:
    """Публикация панели в сеть без пароля — ошибка конфигурации."""
    with pytest.raises(RuntimeError, match="loopback"):
        Site.validate_api_auth_configuration("0.0.0.0", "", "")
    with pytest.raises(RuntimeError, match="loopback"):
        Site.validate_api_auth_configuration("::", "", "")

    # С учётными данными такая привязка допустима.
    Site.validate_api_auth_configuration("0.0.0.0", "admin", "secret")
    # И с loopback без учётных данных тоже.
    Site.validate_api_auth_configuration("127.0.0.1", "", "")


def test_partial_credentials_are_rejected_on_any_bind() -> None:
    """Задано только одно из двух — это ошибка на любой привязке."""
    with pytest.raises(RuntimeError, match="ADMIN_USERNAME"):
        Site.validate_api_auth_configuration("127.0.0.1", "admin", "")
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        Site.validate_api_auth_configuration("127.0.0.1", "", "secret")


def test_basic_auth_username_cannot_contain_colon() -> None:
    """Двоеточие в имени пользователя сделало бы вход невозможным."""
    with pytest.raises(RuntimeError, match="не может содержать"):
        Site.validate_api_auth_configuration("127.0.0.1", "a:b", "secret")
