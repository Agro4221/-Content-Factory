"""Regression tests for temporary-file cleanup when media downloading fails."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import main


class MessageMediaPhoto:
    pass


class FakeMediaMessage:
    def __init__(self, path: Path, *, fail: bool = False) -> None:
        self.media = MessageMediaPhoto()
        self.path = path
        self.fail = fail

    async def download_media(self, *, file: str, progress_callback):
        if self.fail:
            raise RuntimeError("simulated media download failure")
        self.path.write_bytes(b"media")
        return str(self.path)


def test_partial_download_failure_cleans_previously_downloaded_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(main, "TEMP_DIR", str(tmp_path))
    first_path = tmp_path / "first.jpg"
    messages = [
        FakeMediaMessage(first_path),
        FakeMediaMessage(tmp_path / "second.jpg", fail=True),
    ]

    with pytest.raises(RuntimeError, match="simulated media download failure"):
        asyncio.run(main._download_media(messages, caption="caption"))

    assert not first_path.exists()


def test_successful_download_keeps_files_for_caller_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(main, "TEMP_DIR", str(tmp_path))
    path = tmp_path / "photo.jpg"

    media_list, temp_paths = asyncio.run(
        main._download_media([FakeMediaMessage(path)], caption="caption")
    )

    assert path.exists()
    assert temp_paths == [str(path)]
    assert len(media_list) == 1
