"""Тесты статической проверки конфигурации Content Factory."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import Site
from Site import _build_readiness
from state import bot_running


def complete_settings() -> dict[str, str]:
    return {
        "tg_api_id": "12345",
        "tg_api_hash": "example-api-hash",
        "bot_token": "12345:example-bot-token",
        "ai_mode": "LOCAL",
        "local_host": "127.0.0.1",
        "local_port": "1234",
        "local_model": "test-model",
        "sources": "@source_channel\n",
        "topic_channels": json.dumps({"общее": "@target_channel"}, ensure_ascii=False),
        "use_proxy": "0",
    }


def test_complete_configuration_is_ready_when_session_exists() -> None:
    result = _build_readiness(complete_settings(), session_exists=True)

    assert result["ready"] is True
    assert result["blocking_count"] == 0
    assert any(check["id"] == "ai" and check["status"] == "warning" for check in result["checks"])


def test_missing_session_blocks_start_and_gives_recovery_command() -> None:
    result = _build_readiness(complete_settings(), session_exists=False)

    assert result["ready"] is False
    assert result["blocking_count"] == 1
    session_check = next(check for check in result["checks"] if check["id"] == "session")
    assert session_check["status"] == "error"
    assert "tools/create_telegram_session.py" in session_check["detail"]


def test_remote_ai_requires_url_model_and_key() -> None:
    settings = complete_settings()
    settings.update({
        "ai_mode": "REMOTE",
        "remote_url": "https://api.example.test/v1/chat/completions",
        "remote_model": "test-model",
        "remote_api_key": "",
    })

    result = _build_readiness(settings, session_exists=True)
    ai_check = next(check for check in result["checks"] if check["id"] == "ai")

    assert ai_check["status"] == "error"
    assert "API-ключ" in ai_check["detail"]


def test_malformed_destination_json_blocks_start() -> None:
    settings = complete_settings()
    settings["topic_channels"] = "{not valid json"

    result = _build_readiness(settings, session_exists=True)
    target_check = next(check for check in result["checks"] if check["id"] == "targets")

    assert result["ready"] is False
    assert target_check["status"] == "error"


def test_diagnostics_never_return_secret_values() -> None:
    settings = complete_settings()
    settings["remote_api_key"] = "DO_NOT_EXPOSE_THIS"
    result = _build_readiness(settings, session_exists=True)
    serialized = str(result)

    assert "DO_NOT_EXPOSE_THIS" not in serialized
    assert "example-api-hash" not in serialized
    assert "example-bot-token" not in serialized



def test_start_module_refuses_to_run_when_preflight_has_blockers(monkeypatch) -> None:
    monkeypatch.setattr(Site.db, "get_all_settings", lambda: {})
    monkeypatch.setattr(Site.db, "add_log", lambda *_args: None)
    monkeypatch.setattr(
        Site,
        "_build_readiness",
        lambda *_args, **_kwargs: {
            "ready": False,
            "checks": [{
                "id": "session",
                "title": "Сессия Telegram",
                "status": "error",
                "detail": "Создайте сессию Telegram.",
            }],
        },
    )
    bot_running.clear()
    try:
        with pytest.raises(HTTPException) as exc:
            Site._start_module("posts")

        assert exc.value.status_code == 409
        assert "Сессия Telegram" in str(exc.value.detail)
        assert not bot_running.is_set()
    finally:
        bot_running.clear()
