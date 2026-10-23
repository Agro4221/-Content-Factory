"""Проверяет самодостаточность веб-панели и её обязательные элементы."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import Site


@pytest.fixture
def client(monkeypatch):
    with TestClient(
        Site.app,
        client=("127.0.0.1", 12345),
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


def test_admin_page_is_self_contained(client: TestClient) -> None:
    """Панель должна содержать CSS и JavaScript внутри ``admin.html``.

    Внешние файлы ``admin.css`` и ``admin.js`` не используются.
    """
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/admin.css"' not in response.text
    assert 'src="/admin.js"' not in response.text
    assert "<style" in response.text.lower()
    assert "<script>" in response.text.lower()


def test_admin_page_contains_required_controls(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.text

    # Статус, управление парсером и панель действий.
    assert 'id="btn-bot-toggle"' in body
    assert 'id="btn-refresh"' in body
    assert 'id="btn-save"' in body
    assert 'id="runtime-state"' in body
    assert 'class="status-strip"' in body
    assert 'class="action-bar"' in body
    assert 'id="card-bot"' in body
    assert 'id="card-ai"' in body
    assert 'id="card-proxy"' in body
    assert 'id="card-stats"' in body

    # Навигация по разделам и панели разделов.
    for panel in ("quick", "targets", "ai", "proxy", "filters", "logs"):
        assert f'data-panel="{panel}"' in body
    assert 'id="acc-quick"' in body
    assert 'id="acc-targets"' in body
    assert 'id="acc-ai"' in body
    assert 'id="acc-proxy"' in body
    assert 'id="acc-filters"' in body
    assert 'id="acc-logs"' in body

    # Поля Telegram.
    assert 'id="f-tg_api_id"' in body
    assert 'id="f-tg_api_hash"' in body
    assert 'id="f-bot_token"' in body
    assert 'id="f-sources"' in body
    assert 'id="f-poll_delay"' in body

    # Настройки AI.
    assert 'id="f-ai_mode"' in body
    assert 'id="f-remote_url"' in body
    assert 'id="f-remote_api_key"' in body
    assert 'id="f-remote_model"' in body
    assert 'id="f-local_host"' in body
    assert 'id="f-local_port"' in body
    assert 'id="f-local_model"' in body
    assert 'id="f-system_prompt"' in body

    # Прокси.
    assert 'id="f-use_proxy"' in body
    assert 'id="f-proxy_type"' in body
    assert 'id="f-proxy_host"' in body
    assert 'id="f-proxy_port"' in body
    assert 'id="f-proxy_user"' in body
    assert 'id="f-proxy_pass"' in body
    assert 'id="f-mtproto_key"' in body
    assert 'id="proxy-result"' in body

    # Фильтры.
    assert 'id="f-ads_words"' in body
    assert 'id="f-watermark_text"' in body

    # Маршруты «тема → канал», журнал и уведомления.
    assert 'id="topics-list"' in body
    assert 'id="add-topic"' in body
    assert 'id="log-view"' in body
    assert 'id="clear-logs-btn"' in body
    assert 'id="clear-logs-confirm"' in body
    assert 'id="toast-container"' in body

    # Иконка вкладки отдаётся, а не 404-ится.
    assert 'href="/favicon.png"' in body


def test_secret_fields_are_masked_and_never_prefilled(client: TestClient) -> None:
    """Секретные поля не содержат значения и закрыты маской ввода."""
    response = client.get("/")
    body = response.text
    for field_id in (
        "f-tg_api_hash",
        "f-bot_token",
        "f-remote_api_key",
        "f-proxy_pass",
        "f-mtproto_key",
    ):
        match = re.search(r'<input\b[^>]*id="' + field_id + r'"[^>]*>', body)
        assert match is not None, f"{field_id} must be present"
        tag = match.group(0)
        assert 'type="password"' in tag, f"{field_id} must be masked"
        assert "value=" not in tag, f"{field_id} must not carry a value"


@pytest.mark.parametrize("path", ["/admin.css", "/admin.js"])
def test_legacy_admin_assets_are_no_longer_served(
    client: TestClient, path: str
) -> None:
    """Отдельные файлы панели удалены: страница самодостаточна."""
    response = client.get(path)
    assert response.status_code == 404, f"{path} must not be served anymore"
