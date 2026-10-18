"""The admin page and its separate CSS/JS assets are served consistently."""
from __future__ import annotations

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


def test_admin_page_references_external_assets(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/admin.css"' in response.text
    assert 'src="/admin.js"' in response.text
    assert "<style" not in response.text.lower()
    assert "<script>" not in response.text.lower()
    assert 'name="local_ai_provider" id="local_ai_provider"' in response.text
    assert 'name="local_model" id="local_model" required' in response.text
    assert 'id="local_model_manual"' in response.text
    assert 'id="localModelOptions"' not in response.text
    assert 'class="local-ai-advanced"' in response.text
    assert 'name="local_host" id="local_host"' in response.text
    assert 'name="local_port" id="local_port"' in response.text


@pytest.mark.parametrize(
    ("path", "media_type", "marker"),
    [
        ("/admin.css", "text/css", ":root"),
        ("/admin.js", "javascript", "function setupTheme()"),
    ],
)
def test_admin_assets_are_served_with_expected_content_type(
    client: TestClient, path: str, media_type: str, marker: str
) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert media_type in response.headers["content-type"].lower()
    assert marker in response.text
