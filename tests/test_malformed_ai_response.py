"""Regression tests for malformed successful AI responses."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import main


@pytest.mark.parametrize(
    "response",
    [
        "I cannot help with that request.",
        '{"message": "not in the expected structure"}',
        "ТЕМА: новости\nХЭШТЕГИ: #новости",
        "ТЕМА: новости\nТЕКСТ:\nХЭШТЕГИ: #новости",
    ],
)
def test_ai_parser_rejects_responses_without_publishable_text(response: str) -> None:
    with pytest.raises(main.AIResponseFormatError):
        main._parse_ai_response(response, "original source text")


def test_ai_parser_preserves_valid_structured_response() -> None:
    topic, text = main._parse_ai_response(
        "ТЕМА: Новости\nТЕКСТ: Переработанный текст новости.\nХЭШТЕГИ: #новости",
        "original source text",
    )

    assert topic == "новости"
    assert text == "Переработанный текст новости.\n\n#новости"


def test_ai_rewrite_does_not_fallback_to_unstructured_model_output(monkeypatch) -> None:
    logs: list[tuple[str, str]] = []

    class FakeDB:
        def get_setting(self, _key: str, default=""):
            return default

        def add_log(self, log_type: str, message: str) -> None:
            logs.append((log_type, message))

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self) -> dict:
            return {
                "choices": [
                    {"message": {"content": '{"message":"unstructured response"}'}}
                ]
            }

        async def text(self) -> str:
            return ""

    class FakeSession:
        def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(main, "db", FakeDB())
    cfg = {
        "topic_channels": {"новости": "@target"},
        "ai_mode": "LOCAL",
        "local_url": "http://127.0.0.1:1234/v1/chat/completions",
        "local_model": "test-model",
        "remote_url": "",
        "remote_model": "",
        "remote_api_key": "",
    }

    with pytest.raises(main.AIResponseFormatError):
        asyncio.run(main._ai_rewrite("original source text", cfg, FakeSession()))

    assert any(log_type == "ERR-400" for log_type, _ in logs)


def test_malformed_ai_response_is_not_sent_or_marked_as_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[tuple[str, str]] = []
    saved_posts: list[tuple[str, int]] = []
    sent: list[dict] = []

    class FakeDB:
        def add_log(self, log_type: str, message: str) -> None:
            logs.append((log_type, message))

        def save_post(self, source: str, post_id: int) -> None:
            saved_posts.append((source, post_id))

        def save_skipped_post(self, *_args, **_kwargs) -> None:
            raise AssertionError("malformed AI output should stay retryable")

    class Running:
        @staticmethod
        def is_set() -> bool:
            return True

    class FakeBot:
        async def send_message(self, **kwargs) -> None:
            sent.append(kwargs)

        async def send_photo(self, **kwargs) -> None:
            sent.append(kwargs)

        async def send_video(self, **kwargs) -> None:
            sent.append(kwargs)

        async def send_document(self, **kwargs) -> None:
            sent.append(kwargs)

    async def invalid_ai(*_args, **_kwargs):
        raise main.AIResponseFormatError("отсутствует обязательный раздел ТЕКСТ:")

    monkeypatch.setattr(main, "db", FakeDB())
    monkeypatch.setattr(main, "bot_running", Running())
    monkeypatch.setattr(main, "_ai_rewrite", invalid_ai)
    cfg = {
        "ads_words": [],
        "watermark": "",
        "topic_channels": {"новости": "@target"},
    }
    message = SimpleNamespace(id=7, message="original source text", media=None)

    asyncio.run(
        main._process_message_group(None, "@source", [message], cfg, FakeBot(), None)
    )

    assert saved_posts == []
    assert sent == []
    assert len(logs) == 1
    assert logs[0][0] == "WARN-404"

@pytest.mark.parametrize(
    "hashtag_label",
    ["ХЭШТЕГИ:", "хэштеги:", "Хэштеги:"],
)
def test_ai_parser_removes_hashtag_heading_case_insensitively(hashtag_label: str) -> None:
    topic, text = main._parse_ai_response(
        f"ТЕМА: Новости\\nТЕКСТ: Переработанный текст.\\n{hashtag_label} #новости",
        "original source text",
    )

    assert topic == "новости"
    assert text == "Переработанный текст.\\n\\n#новости"

