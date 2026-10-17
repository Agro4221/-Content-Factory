"""Security regression tests for the local Content Factory admin panel."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

import Site


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(Site, "API_HOST", "127.0.0.1")
    monkeypatch.setattr(Site, "ADMIN_USERNAME", "")
    monkeypatch.setattr(Site, "ADMIN_PASSWORD", "")
    with TestClient(
        Site.app,
        client=("127.0.0.1", 12345),
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


def _basic(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def test_loopback_panel_remains_available_without_auth(client: TestClient) -> None:
    response = client.get("/api/bot/status")
    assert response.status_code == 200


def test_external_bind_without_credentials_fails_closed(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(Site, "API_HOST", "0.0.0.0")

    response = client.get("/api/bot/status")

    assert response.status_code == 503
    assert "ADMIN_USERNAME" in response.json()["detail"]


def test_remote_client_through_loopback_proxy_is_rejected_without_auth(monkeypatch) -> None:
    monkeypatch.setattr(Site, "API_HOST", "127.0.0.1")
    monkeypatch.setattr(Site, "ADMIN_USERNAME", "")
    monkeypatch.setattr(Site, "ADMIN_PASSWORD", "")
    with TestClient(
        Site.app,
        base_url="http://127.0.0.1",
        client=("203.0.113.10", 12345),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/api/bot/status")

    assert response.status_code == 503
    assert "защита не настроена" in response.json()["detail"]


def test_partial_admin_credentials_fail_closed(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(Site, "ADMIN_USERNAME", "admin")

    response = client.get("/")

    assert response.status_code == 503
    assert "ADMIN_USERNAME" in response.json()["detail"]


@pytest.mark.parametrize("authorization", [None, "Bearer not-basic", "Basic !!!", _basic("admin", "wrong")])
def test_bad_or_missing_credentials_are_rejected(client: TestClient, monkeypatch, authorization) -> None:
    monkeypatch.setattr(Site, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(Site, "ADMIN_PASSWORD", "correct horse battery staple")
    headers = {"Authorization": authorization} if authorization else {}

    response = client.get("/api/bot/status", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic realm=")


def test_correct_credentials_authorize_api(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(Site, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(Site, "ADMIN_PASSWORD", "pässword-with-unicode")

    response = client.get(
        "/api/bot/status",
        headers={"Authorization": _basic("admin", "pässword-with-unicode")},
    )

    assert response.status_code == 200


def test_remote_http_access_is_rejected_even_with_credentials(monkeypatch) -> None:
    monkeypatch.setattr(Site, "API_HOST", "0.0.0.0")
    monkeypatch.setattr(Site, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(Site, "ADMIN_PASSWORD", "secret")
    with TestClient(
        Site.app,
        base_url="http://panel.example",
        client=("203.0.113.10", 12345),
        raise_server_exceptions=False,
    ) as client:
        response = client.get(
            "/api/bot/status",
            headers={"Authorization": _basic("admin", "secret")},
        )

    assert response.status_code == 403
    assert "HTTPS" in response.json()["detail"]


def test_remote_https_access_requires_valid_credentials(monkeypatch) -> None:
    monkeypatch.setattr(Site, "API_HOST", "0.0.0.0")
    monkeypatch.setattr(Site, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(Site, "ADMIN_PASSWORD", "secret")
    with TestClient(
        Site.app,
        base_url="https://panel.example",
        client=("203.0.113.10", 12345),
        raise_server_exceptions=False,
    ) as client:
        response = client.get(
            "/api/bot/status",
            headers={"Authorization": _basic("admin", "secret")},
        )

    assert response.status_code == 200


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
def test_loopback_host_recognizes_local_addresses(host: str) -> None:
    assert Site._is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "admin.local"])
def test_non_loopback_host_is_never_assumed_local(host: str) -> None:
    assert not Site._is_loopback_host(host)
