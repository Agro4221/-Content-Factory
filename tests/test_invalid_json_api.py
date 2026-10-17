"""Regression tests for malformed JSON on admin API POST routes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import Site


@pytest.fixture
def client():
    with TestClient(Site.app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.parametrize("path", ["/api/settings", "/api/proxy/test"])
def test_malformed_json_returns_400(client: TestClient, path: str) -> None:
    response = client.post(
        path,
        content=b'{"broken":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Некорректный JSON"}


@pytest.mark.parametrize("path", ["/api/settings", "/api/proxy/test"])
def test_json_non_object_still_returns_400(client: TestClient, path: str) -> None:
    response = client.post(
        path,
        content=b"[]",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Ожидается JSON-объект"}
