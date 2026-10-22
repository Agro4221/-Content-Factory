"""Regression tests for routing downloaded document files to Telegram documents."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.types import (
    FSInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)

import main


@pytest.mark.parametrize(
    ("path", "expected_type"),
    [
        ("photo.jpg", InputMediaPhoto),
        ("photo.jpeg", InputMediaPhoto),
        ("photo.png", InputMediaPhoto),
        ("photo.webp", InputMediaPhoto),
        ("clip.mp4", InputMediaVideo),
        ("clip.mov", InputMediaDocument),
        ("clip.gif", InputMediaDocument),
        ("clip.mkv", InputMediaDocument),
        ("clip.webm", InputMediaDocument),
        ("report.pdf", InputMediaDocument),
        ("track.mp3", InputMediaDocument),
        ("voice.ogg", InputMediaDocument),
        ("archive.zip", InputMediaDocument),
        ("attachment", InputMediaDocument),
    ],
)
def test_classify_extension_does_not_treat_documents_as_photos(
    path: str,
    expected_type: type,
) -> None:
    assert main._classify_extension(path) is expected_type


def test_single_document_is_sent_with_send_document(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    document_path = tmp_path / "report.pdf"
    document_path.write_bytes(b"pdf-data")
    media = InputMediaDocument(
        media=FSInputFile(str(document_path)),
        caption="rewritten caption",
    )
    sent: list[dict] = []
    saved: list[tuple[str, int]] = []

    class FakeBot:
        async def send_document(self, **kwargs) -> None:
            sent.append(kwargs)

        async def send_photo(self, **kwargs) -> None:
            raise AssertionError("document must not be sent as a photo")

        async def send_video(self, **kwargs) -> None:
            raise AssertionError("document must not be sent as a video")

    class FakeDB:
        def save_post(self, source: str, post_id: int) -> None:
            saved.append((source, post_id))

        def add_log(self, *_args, **_kwargs) -> None:
            pass

    class Running:
        @staticmethod
        def is_set() -> bool:
            return True

    async def fake_ai_rewrite(*_args, **_kwargs) -> tuple[str, str]:
        return "news", "rewritten caption"

    async def fake_download(*_args, **_kwargs):
        return [media], []

    async def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(main, "db", FakeDB())
    monkeypatch.setattr(main, "bot_running", Running())
    monkeypatch.setattr(main, "_ai_rewrite", fake_ai_rewrite)
    monkeypatch.setattr(main, "_download_media", fake_download)
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)
    cfg = {
        "ads_words": [],
        "watermark": "",
        "topic_channels": {"news": "@target"},
    }
    message = SimpleNamespace(id=7, message="source caption", media=None)

    asyncio.run(
        main._process_message_group(None, "@source", [message], cfg, FakeBot(), None)
    )

    assert len(sent) == 1
    assert sent[0]["chat_id"] == "@target"
    assert sent[0]["document"] is media.media
    assert sent[0]["caption"] == "rewritten caption"
    assert saved == [("@source", 7)]

def test_mixed_document_and_visual_album_is_normalized_to_documents() -> None:
    media = [
        InputMediaPhoto(media="photo-file-id", caption="album caption"),
        InputMediaDocument(media="document-file-id"),
        InputMediaVideo(media="video-file-id"),
    ]

    normalized = main._prepare_media_group(media)

    assert len(normalized) == 3
    assert all(isinstance(item, InputMediaDocument) for item in normalized)
    assert [item.media for item in normalized] == [
        "photo-file-id",
        "document-file-id",
        "video-file-id",
    ]
    assert normalized[0].caption == "album caption"
    assert normalized[1].caption is None
    assert normalized[2].caption is None


def test_photo_video_only_album_keeps_native_media_types() -> None:
    media = [
        InputMediaPhoto(media="photo-file-id"),
        InputMediaVideo(media="video-file-id"),
    ]

    normalized = main._prepare_media_group(media)

    assert isinstance(normalized[0], InputMediaPhoto)
    assert isinstance(normalized[1], InputMediaVideo)

