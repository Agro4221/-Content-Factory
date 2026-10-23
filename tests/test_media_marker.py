"""Проверяет маркировку медиафайлов и привязку к исходным постам.

Метка в имени файла позволяет отличать вложения разных постов и не
публиковать медиа, не относящееся к текущей записи. Файлы без метки
поддерживаются для обратной совместимости.
"""
from __future__ import annotations

import media_marker


def test_marker_contains_source_and_post_id() -> None:
    marker = media_marker.media_marker("@donor_channel", 101)
    assert marker.startswith("p")
    assert "donor" in marker
    assert "101" in marker


def test_marker_has_no_underscores() -> None:
    """Подчёркивание в метке ломало бы разбор имени файла."""
    assert "_" not in media_marker.media_marker("@a_b", "12_34")


def test_file_name_contains_marker() -> None:
    marker = media_marker.media_marker("@donor", 7)
    name = media_marker.media_file_name(marker, 1730000000000, 0, ".jpg")
    assert name.startswith(marker + "_")
    assert name.endswith(".jpg")
    assert media_marker.file_marker(name) == marker


def test_file_without_marker_is_recognised() -> None:
    assert media_marker.file_marker("1730000000000_0.jpg") == ""
    assert media_marker.has_marker("1730000000000_0.jpg") is False


def test_own_file_belongs_to_its_post() -> None:
    marker = media_marker.media_marker("@donor", 7)
    name = media_marker.media_file_name(marker, 1, 0, ".jpg")
    assert media_marker.belongs_to_post(name, marker) is True


def test_foreign_file_is_rejected() -> None:
    """Медиафайл другого поста не должен отправляться с текущим текстом."""
    mine = media_marker.media_marker("@donor", 7)
    theirs = media_marker.media_file_name(
        media_marker.media_marker("@donor", 8), 1, 0, ".jpg"
    )
    assert media_marker.belongs_to_post(theirs, mine) is False


def test_legacy_file_is_allowed() -> None:
    """Файлы, созданные до маркировки, продолжают публиковаться."""
    assert media_marker.belongs_to_post("1730000000000_0.jpg", "pdonor-7") is True


def test_different_sources_get_different_markers() -> None:
    assert media_marker.media_marker("@a", 1) != media_marker.media_marker("@b", 1)
    assert media_marker.media_marker("@a", 1) != media_marker.media_marker("@a", 2)


# ---------- Имя, которое уходит в канал ----------

def test_marker_is_removed_from_the_name_shown_in_channel() -> None:
    """Маркировка нужна внутри, но пользователь её видеть не должен."""
    marker = media_marker.media_marker("@donor", 7)
    file_name = media_marker.media_file_name(marker, 1730000000000, 1, ".jpg")
    assert marker in file_name

    clean = media_marker.clean_filename(file_name)
    assert marker not in clean
    assert clean == "1730000000000_1.jpg"


def test_clean_name_keeps_plain_files_as_they_are() -> None:
    assert media_marker.clean_filename("photo.jpg") == "photo.jpg"
    assert media_marker.clean_filename("1730000000000_0.jpg") == "1730000000000_0.jpg"


def test_clean_name_never_returns_empty() -> None:
    assert media_marker.clean_filename("") == "media"
    assert media_marker.clean_filename("pdonor-7") == "media"


def test_clean_name_drops_directories() -> None:
    assert media_marker.clean_filename("../../secret.jpg") == "secret.jpg"


# ---------- Что реально уходит в Telegram ----------

def test_marker_does_not_reach_telegram(monkeypatch, tmp_path) -> None:
    """Проверка на настоящей отправке: имя файла в Telegram чистое."""
    import asyncio

    import main

    queue_dir = tmp_path / "queue_media"
    queue_dir.mkdir()
    marker = media_marker.media_marker("@donor", 7)
    file_name = media_marker.media_file_name(marker, 1730000000000, 0, ".jpg")
    (queue_dir / file_name).write_bytes(b"fake-jpeg")

    monkeypatch.setattr(main, "_queue_media_dir", lambda: str(queue_dir))

    class FakeBot:
        def __init__(self) -> None:
            self.sent: list = []

        async def send_document(self, chat_id, document, caption=None, **_kwargs):
            self.sent.append(document)

    bot = FakeBot()
    post = {
        "id": 1,
        "source_name": "@donor",
        "post_id": "7",
        "target_chat": "@target",
        "target_chats": "[]",
        "publish_text": "Текст поста",
        "media_files": f'["{file_name}"]',
        "media_kind": "document",
    }

    ok, _message = asyncio.run(main.publish_queued_post(bot, post))

    assert ok is True
    assert len(bot.sent) == 1
    sent_name = bot.sent[0].filename
    assert marker not in sent_name
    assert sent_name == "1730000000000_0.jpg"


def test_foreign_media_is_not_published_with_the_post(monkeypatch, tmp_path) -> None:
    """Медиа с чужой меткой не уезжает вместе с этим постом."""
    import asyncio

    import main

    queue_dir = tmp_path / "queue_media"
    queue_dir.mkdir()
    foreign = media_marker.media_file_name(
        media_marker.media_marker("@donor", 8), 1730000000000, 0, ".jpg"
    )
    (queue_dir / foreign).write_bytes(b"fake-jpeg")

    monkeypatch.setattr(main, "_queue_media_dir", lambda: str(queue_dir))

    class FakeBot:
        def __init__(self) -> None:
            self.sent: list = []

        async def send_document(self, chat_id, document, caption=None, **_kwargs):
            self.sent.append(document)

        async def send_message(self, chat_id, text, **_kwargs):
            self.sent.append(text)

    bot = FakeBot()
    post = {
        "id": 1,
        "source_name": "@donor",
        "post_id": "7",
        "target_chat": "@target",
        "target_chats": "[]",
        "publish_text": "Текст поста",
        "media_files": f'["{foreign}"]',
        "media_kind": "document",
    }

    ok, _message = asyncio.run(main.publish_queued_post(bot, post))

    # Медиа пропущено, но текст всё равно опубликован.
    assert ok is True
    assert bot.sent == ["Текст поста"]
