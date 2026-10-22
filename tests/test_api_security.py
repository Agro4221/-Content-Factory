"""Защита локального API панели.

Панель не имеет пароля (приложение локальное, пользователь не должен
каждый раз вводить логин). Защита построена на трёх уровнях:

1. проверка Host — против DNS-rebinding;
2. проверка Origin / Sec-Fetch-Site / служебного заголовка — против CSRF;
3. защитные HTTP-заголовки — против кликджекинга и внедрения.

Тесты помечены маркером ``real_security``: для них служебные послабления
conftest.py не применяются.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api_security
import Site

pytestmark = pytest.mark.real_security


@pytest.fixture
def client():
    with TestClient(
        Site.app,
        client=("127.0.0.1", 12345),
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


# ---------- Host ----------

def test_request_from_foreign_host_is_rejected(client: TestClient) -> None:
    """Запрос с чужим Host — это признак DNS-rebinding."""
    response = client.get("/api/settings", headers={"Host": "evil.example.com"})
    assert response.status_code == 400


def test_request_from_localhost_is_allowed(client: TestClient) -> None:
    response = client.get("/api/settings", headers={"Host": "127.0.0.1:8000"})
    assert response.status_code == 200


def test_request_with_ipv6_localhost_is_allowed(client: TestClient) -> None:
    response = client.get("/api/settings", headers={"Host": "[::1]:8000"})
    assert response.status_code == 200


def test_empty_host_is_rejected() -> None:
    assert api_security.is_allowed_host(None, {"127.0.0.1"}) is False
    assert api_security.is_allowed_host("", {"127.0.0.1"}) is False


# ---------- CSRF ----------

def test_cross_site_post_is_rejected(client: TestClient) -> None:
    """POST без признаков «свой запрос» — типичный CSRF со стороны сайта."""
    response = client.post(
        "/api/settings",
        json={"poll_delay": "60"},
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 403


def test_cross_site_post_with_sec_fetch_site_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/settings",
        json={"poll_delay": "60"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_same_origin_post_is_allowed(client: TestClient) -> None:
    response = client.post(
        "/api/settings",
        json={"poll_delay": "60"},
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200


def test_same_origin_header_post_is_allowed(client: TestClient) -> None:
    """Панель добавляет служебный заголовок ко всем своим запросам."""
    response = client.post(
        "/api/settings",
        json={"poll_delay": "60"},
        headers={api_security.CLIENT_HEADER: "1"},
    )
    assert response.status_code == 200


def test_foreign_origin_post_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/settings",
        json={"poll_delay": "60"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert response.status_code == 403


def test_get_is_not_blocked_by_csrf_rules(client: TestClient) -> None:
    """Чтение не меняет состояние — CSRF к нему неприменим."""
    response = client.get("/api/settings")
    assert response.status_code == 200


# ---------- Заголовки ----------

def test_security_headers_are_present(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_api_documentation_is_not_published(client: TestClient) -> None:
    """Схема всех эндпоинтов не должна быть доступна никому подряд."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


# ---------- Предел размера тела ----------

def test_oversized_body_is_rejected(client: TestClient) -> None:
    payload = "x" * (api_security.MAX_BODY_BYTES + 1024)
    response = client.post(
        "/api/settings",
        content=payload,
        headers={"Content-Type": "application/json", api_security.CLIENT_HEADER: "1"},
    )
    assert response.status_code == 413


def test_body_limit_helper() -> None:
    assert api_security.body_too_large(None) is False
    assert api_security.body_too_large("100") is False
    assert api_security.body_too_large(str(api_security.MAX_BODY_BYTES)) is False
    assert api_security.body_too_large(str(api_security.MAX_BODY_BYTES + 1)) is True
    # Мусор в заголовке не должен приводить к обходу проверки.
    assert api_security.body_too_large("не число") is True
