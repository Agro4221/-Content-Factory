"""Проверяет приём, валидацию и сохранение настройки таймаута AI.

Пустое значение должно сохраняться как пустое, чтобы приложение продолжало
использовать актуальное значение по умолчанию из конфигурации.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import Site
from config import AI_REQUEST_TIMEOUT_S, AI_TIMEOUT_MAX_S, AI_TIMEOUT_MIN_S


class FakeDB:
    """Минимальная база: панели нужны только чтение и запись настроек."""

    def __init__(self) -> None:
        self.settings: dict[str, str] = {"bot_token": ""}

    def get_all_settings(self) -> dict[str, str]:
        return dict(self.settings)

    def get_masked_settings(self) -> dict[str, str]:
        return dict(self.settings)

    def set_settings(self, settings: dict[str, str]) -> None:
        self.settings.update(settings)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value


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


def test_ai_timeout_is_accepted_and_saved(client: TestClient) -> None:
    response = client.post("/api/settings", json={"ai_timeout": "600"})
    assert response.status_code == 200, response.text
    assert client.fake_db.settings["ai_timeout"] == "600"


def test_ai_timeout_is_not_silently_rejected(client: TestClient) -> None:
    """Ключ должен входить в список принимаемых настроек, а не отбрасываться."""
    response = client.post("/api/settings", json={"ai_timeout": "120"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "ai_timeout" not in payload.get("rejected", [])


@pytest.mark.parametrize("value", ["1", "0", "-5", "99999", "abc", "1.5"])
def test_ai_timeout_outside_range_is_rejected(client: TestClient, value: str) -> None:
    response = client.post("/api/settings", json={"ai_timeout": value})
    assert response.status_code == 422, response.text
    assert client.fake_db.settings.get("ai_timeout") is None


def test_ai_timeout_empty_falls_back_to_default(client: TestClient) -> None:
    """Пустое поле = значение по умолчанию, а не ошибка валидации.

    В базу при этом пишется пустая строка, а не сам дефолт: иначе после
    первого сохранения настройка из .env перестала бы влиять.
    """
    response = client.post("/api/settings", json={"ai_timeout": ""})
    assert response.status_code == 200, response.text
    assert client.fake_db.settings["ai_timeout"] == ""


def test_ai_timeout_empty_is_resolved_by_the_application() -> None:
    """Приложение само подставляет дефолт, когда в базе значение пустое."""
    import main

    assert main._ai_timeout_seconds("") == AI_REQUEST_TIMEOUT_S
    assert main._ai_timeout_seconds(None) == AI_REQUEST_TIMEOUT_S
    assert main._ai_timeout_seconds("600") == 600
    # Некорректное значение тоже не должно ломать запуск.
    assert main._ai_timeout_seconds("abc") == AI_REQUEST_TIMEOUT_S
    assert main._ai_timeout_seconds("5") == AI_REQUEST_TIMEOUT_S
    # Слишком большое значение ограничивается верхней границей.
    assert main._ai_timeout_seconds("999999") == AI_TIMEOUT_MAX_S


def test_ai_timeout_defaults_endpoint_exposes_default(client: TestClient) -> None:
    response = client.get("/api/settings/defaults")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ai_timeout"] == AI_REQUEST_TIMEOUT_S
    assert payload["system_prompt"]


def test_ai_timeout_bounds_are_consistent() -> None:
    assert AI_TIMEOUT_MIN_S <= AI_REQUEST_TIMEOUT_S <= AI_TIMEOUT_MAX_S
