"""Regression tests for admin API authentication and network-bind safety."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import Site


@pytest.fixture
def client() -> TestClient:
    with TestClient(Site.app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_localhost_panel_remains_usable_without_credentials(monkeypatch, client: TestClient) -> None:
    monkeypatch.setattr(Site, "ADMIN_USERNAME", "")
    monkeypatch.setattr(Site, "ADMIN_PASSWORD", "")

    response = client.get("/api/bot/status")

    assert response.status_code == 200


def test_basic_auth_protects_admin_html_and_api(monkeypatch, client: TestClient) -> None:
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
    assert not Site._basic_auth_matches(None, "admin", "password")
    assert not Site._basic_auth_matches("Bearer token", "admin", "password")
    assert not Site._basic_auth_matches("Basic not-base64!", "admin", "password")
    assert not Site._basic_auth_matches("Basic YWRtaW4=", "admin", "password")


def test_network_bind_requires_both_credentials() -> None:
    with pytest.raises(RuntimeError, match="non-loopback"):
        Site._validate_api_auth_configuration("0.0.0.0", "", "")
    with pytest.raises(RuntimeError, match="non-loopback"):
        Site._validate_api_auth_configuration("::", "admin", "")

    Site._validate_api_auth_configuration("0.0.0.0", "admin", "secret")
    Site._validate_api_auth_configuration("127.0.0.1", "", "")


def test_partial_credentials_are_rejected_on_any_bind() -> None:
    with pytest.raises(RuntimeError, match="both ADMIN_USERNAME and ADMIN_PASSWORD"):
        Site._validate_api_auth_configuration("127.0.0.1", "admin", "")
    with pytest.raises(RuntimeError, match="both ADMIN_USERNAME and ADMIN_PASSWORD"):
        Site._validate_api_auth_configuration("127.0.0.1", "", "secret")


def test_basic_auth_username_cannot_contain_colon() -> None:
    with pytest.raises(RuntimeError, match="cannot contain"):
        Site._validate_api_auth_configuration("127.0.0.1", "a:b", "secret")
