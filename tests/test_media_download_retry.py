"""Regression tests for failed Telegram attachment downloads."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import main


class MessageMediaDocument:
    pass


class FakeDownloadMessage:
    def __init__(
        self,
        message_id: int,
        path: Path,
        *,
        fail: bool = False,
    ) -> None:
        self.id = message_id
        self.media = MessageMediaDocument()
        self.path = path
        self.fail = fail

    async def download_media(self, *, file: str, progress_callback):
        if self.fail:
            return None
        self.path.write_bytes(b"downloaded")
        return str(self.path)


def test_missing_download_path_raises_and_cleans_partial_downloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(main, "TEMP_DIR", str(tmp_path))
    first_path = tmp_path / "first.pdf"
    messages = [
        FakeDownloadMessage(10, first_path),
        FakeDownloadMessage(11, tmp_path / "second.pdf", fail=True),
    ]

    with pytest.raises(main.MediaDownloadError, match="сообщения 11"):
        asyncio.run(main._download_media(messages, caption="caption"))

    assert not first_path.exists()


def test_failed_download_does_not_send_or_mark_post_published(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[tuple[str, str]] = []
    saved_posts: list[tuple[str, int]] = []
    sent_messages: list[dict] = []

    class FakeDB:
        def add_log(self, log_type: str, message: str) -> None:
            logs.append((log_type, message))

        def save_post(self, source: str, post_id: int) -> None:
            saved_posts.append((source, post_id))

        def save_skipped_post(self, *_args, **_kwargs) -> None:
            raise AssertionError("failed download should remain retryable")

    class Running:
        @staticmethod
        def is_set() -> bool:
            return True

    class FakeBot:
        async def send_message(self, **kwargs) -> None:
            sent_messages.append(kwargs)

        async def send_photo(self, **kwargs) -> None:
            sent_messages.append(kwargs)

        async def send_video(self, **kwargs) -> None:
            sent_messages.append(kwargs)

        async def send_document(self, **kwargs) -> None:
            sent_messages.append(kwargs)

    async def fake_ai_rewrite(*_args, **_kwargs) -> tuple[str, str]:
        return "news", "rewritten text"

    async def failed_download(*_args, **_kwargs):
        raise main.MediaDownloadError("Telethon не вернул файл для сообщения 7")

    monkeypatch.setattr(main, "db", FakeDB())
    monkeypatch.setattr(main, "bot_running", Running())
    monkeypatch.setattr(main, "_ai_rewrite", fake_ai_rewrite)
    monkeypatch.setattr(main, "_download_media", failed_download)

    cfg = {
        "ads_words": [],
        "watermark": "",
        "topic_channels": {"news": "@target"},
    }
    message = SimpleNamespace(id=7, message="source caption")

    asyncio.run(
        main._process_message_group(None, "@source", [message], cfg, FakeBot(), None)
    )

    assert saved_posts == []
    assert sent_messages == []
    assert len(logs) == 1
    assert logs[0][0] == "WARN-501"
