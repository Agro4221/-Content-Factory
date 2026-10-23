"""API-контракт панели управления: секреты не показываются, журнал удаляется
только по явному подтверждению.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import Site
from config import SECRET_CLEAR_SIGNAL, SECRET_MASK


class FakeDB:
    """Тестовая реализация слоя данных для проверки контракта API."""

    def __init__(self) -> None:
        self.settings: dict[str, str] = {
            "bot_token": "123456:AAExampleTokenValue",
            "tg_api_hash": "aabbccddeeff00112233445566778899",
            "remote_api_key": "sk-example-key-value",
            "proxy_pass": "proxy-password",
            "mtproto_key": "00112233445566778899aabbccddeeff",
            "use_proxy": "0",
            "poll_delay": "60",
        }
        self.logs: list[tuple[str, str, str]] = [
            ("2026-01-01 00:00:00", "OK-501", "Пост опубликован"),
            ("2026-01-01 00:00:01", "ERR-400", "AI вернул пустой ответ"),
        ]
        self.writes: dict[str, str] = {}
        self.cleared = False

    def get_all_settings(self) -> dict[str, str]:
        return dict(self.settings)

    def get_masked_settings(self) -> dict[str, str]:
        masked = dict(self.settings)
        for key in ("bot_token", "tg_api_hash", "remote_api_key", "proxy_pass", "mtproto_key"):
            if key in masked:
                masked[key] = SECRET_MASK if masked[key] else ""
        return masked

    def set_settings(self, settings: dict[str, str]) -> None:
        self.writes.update(settings)
        self.settings.update(settings)

    def set_setting(self, key: str, value: str) -> None:
        self.writes[key] = value
        self.settings[key] = value

    def get_logs(self, limit: int = 30, offset: int = 0):
        return self.logs[:limit]

    def clear_logs(self) -> int:
        removed = len(self.logs)
        self.logs.clear()
        self.cleared = True
        return removed


@pytest.fixture
def client(monkeypatch):
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)
    with TestClient(
        Site.app,
        client=("127.0.0.1", 12345),
        raise_server_exceptions=False,
    ) as test_client:
        test_client.fake_db = fake_db
        yield test_client


# ---------- Секреты в API ----------

def test_settings_endpoint_never_returns_secret_values(client: TestClient) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.text
    for secret in (
        "123456:AAExampleTokenValue",
        "aabbccddeeff00112233445566778899",
        "sk-example-key-value",
        "proxy-password",
        "00112233445566778899aabbccddeeff",
    ):
        assert secret not in body
    assert SECRET_MASK in body


def test_empty_secret_keeps_stored_value(client: TestClient) -> None:
    response = client.post("/api/settings", json={"bot_token": "", "poll_delay": "90"})
    assert response.status_code == 200
    payload = response.json()
    assert "bot_token" not in client.fake_db.writes
    assert client.fake_db.settings["bot_token"] == "123456:AAExampleTokenValue"
    assert payload.get("kept_secrets") == ["bot_token"]


def test_masked_secret_keeps_stored_value(client: TestClient) -> None:
    response = client.post("/api/settings", json={"bot_token": SECRET_MASK})
    assert response.status_code == 200
    assert "bot_token" not in client.fake_db.writes
    assert client.fake_db.settings["bot_token"] == "123456:AAExampleTokenValue"


def test_new_secret_value_is_saved(client: TestClient) -> None:
    new_token = "123456:AAReplacementTokenValue"
    response = client.post("/api/settings", json={"bot_token": new_token})
    assert response.status_code == 200
    assert client.fake_db.writes["bot_token"] == new_token


def test_clear_signal_removes_stored_secret(client: TestClient) -> None:
    response = client.post("/api/settings", json={"bot_token": SECRET_CLEAR_SIGNAL})
    assert response.status_code == 200
    assert client.fake_db.writes["bot_token"] == ""


def test_regular_settings_are_saved_normally(client: TestClient) -> None:
    response = client.post("/api/settings", json={"poll_delay": "120", "sources": "@mychannel"})
    assert response.status_code == 200
    assert client.fake_db.writes["poll_delay"] == "120"
    assert client.fake_db.writes["sources"] == "@mychannel"


# ---------- Журнал ----------

def test_clear_logs_requires_confirmation(client: TestClient) -> None:
    response = client.post("/api/logs/clear", json={})
    assert response.status_code == 400
    assert client.fake_db.cleared is False
    assert len(client.fake_db.logs) == 2


def test_clear_logs_deletes_records_with_confirmation(client: TestClient) -> None:
    response = client.post("/api/logs/clear", json={"confirm": True})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["removed"] == 2
    assert client.fake_db.cleared is True
    assert client.fake_db.logs == []


def test_clear_logs_rejects_non_json_body(client: TestClient) -> None:
    response = client.post(
        "/api/logs/clear",
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert client.fake_db.cleared is False
