"""Проверяет повторное добавление поста в очередь.

При повторной обработке текст публикации и параметры маршрутизации должны
обновляться. Иначе после восстановления AI-провайдера в очередь может
попасть исходный текст вместо подготовленного пересказа.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from database import DB


@pytest.fixture
def db_without_connection(tmp_path: Path) -> DB:
    db = DB(str(tmp_path / "queue.db"))
    yield db
    db.close()


def _post(**overrides) -> dict:
    post = {
        "source_name": "@donor",
        "post_id": "42",
        "group_id": "42",
        "original_text": "Исходный текст",
        "publish_text": "Исходный текст",
        "media_files": "[]",
        "media_kind": "",
        "category": "общее",
        "target_chat": "-1001",
    }
    post.update(overrides)
    return post


def test_requeue_updates_rewritten_text(db_without_connection: DB) -> None:
    """Переписанный текст ИИ должен заменить исходный."""
    post_id = db_without_connection.save_post_to_queue(_post())

    db_without_connection.save_post_to_queue(
        _post(
            publish_text="Переписанный текст ИИ",
            category="новости",
            target_chat="-100999",
        )
    )

    row = db_without_connection.get_queued_post(post_id)
    assert row is not None
    assert row["publish_text"] == "Переписанный текст ИИ"
    assert row["category"] == "новости"
    assert row["target_chat"] == "-100999"


def test_requeue_keeps_cancelled_post_cancelled(db_without_connection: DB) -> None:
    """Отмена — решение пользователя, повторный сбор её не отменяет."""
    post_id = db_without_connection.save_post_to_queue(_post())
    db_without_connection.update_queued_post(post_id, {"status": "cancelled"})

    db_without_connection.save_post_to_queue(_post(publish_text="Новый текст"))

    row = db_without_connection.get_queued_post(post_id)
    assert row is not None
    assert row["status"] == "cancelled"
    # Текст при этом обновился — вернуться к нему можно вручную.
    assert row["publish_text"] == "Новый текст"


def test_requeue_revives_failed_post(db_without_connection: DB) -> None:
    """Упавший пост с новым содержимым должен снова стать черновиком."""
    post_id = db_without_connection.save_post_to_queue(_post())
    db_without_connection.update_queued_post(post_id, {"status": "failed"})

    db_without_connection.save_post_to_queue(_post(publish_text="Исправленный текст"))

    row = db_without_connection.get_queued_post(post_id)
    assert row is not None
    assert row["status"] == "draft"


def test_requeue_does_not_reset_published_post(db_without_connection: DB) -> None:
    """Опубликованный пост не должен возвращаться в черновики."""
    post_id = db_without_connection.save_post_to_queue(_post())
    db_without_connection.update_queued_post(post_id, {"status": "published"})

    db_without_connection.save_post_to_queue(_post(publish_text="Другой текст"))

    row = db_without_connection.get_queued_post(post_id)
    assert row is not None
    assert row["status"] == "published"


def test_pending_queue_entry_ignores_failed_post(db_without_connection: DB) -> None:
    """Записи, которой ещё нужно работать, определяются по статусу."""
    post_id = db_without_connection.save_post_to_queue(_post())
    assert db_without_connection.is_post_queued("@donor", 42) is True

    db_without_connection.update_queued_post(post_id, {"status": "failed"})
    assert db_without_connection.is_post_queued("@donor", 42) is False

    db_without_connection.update_queued_post(post_id, {"status": "cancelled"})
    assert db_without_connection.is_post_queued("@donor", 42) is True


def test_queued_post_can_be_found_by_source(db_without_connection: DB) -> None:
    db_without_connection.save_post_to_queue(_post(media_files=json.dumps(["a.jpg"])))
    row = db_without_connection.get_queued_post_by_source("@donor", 42)
    assert row is not None
    assert json.loads(row["media_files"]) == ["a.jpg"]
    assert db_without_connection.get_queued_post_by_source("@donor", 999) is None
