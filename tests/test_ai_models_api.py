"""Tests for safe, provider-specific local AI model discovery."""
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


class FakeResponse:
    def __init__(self, payload, status: int = 200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, content_type=None):
        return self.payload


class FakeClientSession:
    def __init__(self, *, payload, calls, status=200, **_kwargs):
        self.payload = payload
        self.calls = calls
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def get(self, url):
        self.calls.append(url)
        return FakeResponse(self.payload, self.status)


def install_fake_session(monkeypatch, payload, calls, status=200):
    def session_factory(**kwargs):
        return FakeClientSession(payload=payload, calls=calls, status=status, **kwargs)

    monkeypatch.setattr(Site.aiohttp, "ClientSession", session_factory)


def test_ollama_returns_installed_model_names_and_uses_fixed_loopback_url(client, monkeypatch):
    calls = []
    install_fake_session(
        monkeypatch,
        {"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.2:3b"}, {"name": "qwen2.5:7b"}]},
        calls,
    )

    response = client.get("/api/ai/models?provider=ollama")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "ollama",
        "connected": True,
        "models": ["qwen2.5:7b", "llama3.2:3b"],
    }
    assert calls == ["http://127.0.0.1:11434/api/tags"]


def test_lm_studio_returns_model_ids_and_uses_fixed_loopback_url(client, monkeypatch):
    calls = []
    install_fake_session(monkeypatch, {"data": [{"id": "local-model"}, {"id": "another-model"}]}, calls)

    response = client.get("/api/ai/models?provider=lmstudio")

    assert response.status_code == 200
    assert response.json()["models"] == ["local-model", "another-model"]
    assert calls == ["http://127.0.0.1:1234/v1/models"]


def test_unknown_provider_is_rejected_without_network_request(client, monkeypatch):
    calls = []
    install_fake_session(monkeypatch, {"models": []}, calls)

    response = client.get("/api/ai/models?provider=http://attacker.example")

    assert response.status_code == 400
    assert calls == []


def test_unavailable_local_provider_returns_clear_error(client, monkeypatch):
    calls = []
    install_fake_session(monkeypatch, {}, calls, status=503)

    response = client.get("/api/ai/models?provider=ollama")

    assert response.status_code == 503
    assert response.json()["models"] == []
    assert "Ollama" in response.json()["detail"]
