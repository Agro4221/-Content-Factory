"""Очередь постов: сохранение, правка, отложенная публикация, отмена."""
from __future__ import annotations

import json

import pytest

import main
from database import DB


@pytest.fixture
def db(tmp_path):
    database = DB(str(tmp_path / "posts.db"))
    yield database
    database.conn.close()


def _make_post(source="@donor", post_id="101", text="Текст поста", target="@target"):
    return {
        "source_name": source,
        "post_id": post_id,
        "group_id": "",
        "original_text": text,
        "publish_text": text,
        "media_files": "[]",
        "media_kind": "",
        "category": "общее",
        "target_chat": target,
    }


def test_post_is_saved_to_queue_as_draft(db: DB) -> None:
    post_id = db.save_post_to_queue(_make_post())
    assert post_id > 0

    stored = db.get_queued_post(post_id)
    assert stored is not None
    assert stored["status"] == "draft"
    assert stored["publish_text"] == "Текст поста"
    assert stored["target_chat"] == "@target"
    assert stored["category"] == "общее"


def test_source_post_can_be_detected_after_queueing(db: DB) -> None:
    assert not db.is_post_queued("@donor", "101")

    post_id = db.save_post_to_queue(_make_post())

    assert db.is_post_queued("@donor", 101)
    assert db.is_post_queued("@donor", "101")
    assert not db.is_post_queued("@other", "101")
    assert db.delete_queued_post(post_id)
    assert not db.is_post_queued("@donor", "101")


def test_same_source_post_is_not_duplicated(db: DB) -> None:
    first = db.save_post_to_queue(_make_post())
    second = db.save_post_to_queue(_make_post(text="Обновлённый текст"))
    assert first == second
    assert len(db.list_queued_posts()) == 1
    assert db.get_queued_post(first)["original_text"] == "Обновлённый текст"


def test_post_can_be_edited(db: DB) -> None:
    post_id = db.save_post_to_queue(_make_post())
    assert db.update_queued_post(
        post_id,
        {"publish_text": "Правленый текст", "category": "TECH", "target_chat": "@other"},
    )
    stored = db.get_queued_post(post_id)
    assert stored["publish_text"] == "Правленый текст"
    assert stored["category"] == "TECH"
    assert stored["target_chat"] == "@other"


def test_post_can_be_scheduled_and_cancelled(db: DB) -> None:
    post_id = db.save_post_to_queue(_make_post())
    db.update_queued_post(
        post_id, {"status": "scheduled", "scheduled_at": "2026-01-01 10:00:00"}
    )
    assert db.get_queued_post(post_id)["status"] == "scheduled"

    db.update_queued_post(post_id, {"status": "cancelled", "scheduled_at": ""})
    assert db.get_queued_post(post_id)["status"] == "cancelled"


def test_due_scheduled_posts_are_returned_by_deadline(db: DB) -> None:
    post_id = db.save_post_to_queue(_make_post())
    db.update_queued_post(
        post_id, {"status": "scheduled", "scheduled_at": "2000-01-01 00:00:00"}
    )
    due = db.due_scheduled_posts()
    assert [row["id"] for row in due] == [post_id]

    # Пост в будущем не попадает в список.
    other = db.save_post_to_queue(_make_post(post_id="102"))
    db.update_queued_post(
        other, {"status": "scheduled", "scheduled_at": "2999-01-01 00:00:00"}
    )
    assert other not in [row["id"] for row in db.due_scheduled_posts()]


def test_post_can_be_deleted(db: DB) -> None:
    post_id = db.save_post_to_queue(_make_post())
    assert db.delete_queued_post(post_id) is True
    assert db.get_queued_post(post_id) is None
    assert db.delete_queued_post(post_id) is False


def test_counts_group_posts_by_status(db: DB) -> None:
    first = db.save_post_to_queue(_make_post(post_id="1"))
    second = db.save_post_to_queue(_make_post(post_id="2"))
    db.update_queued_post(second, {"status": "published"})

    counts = db.count_queued_posts()
    assert counts.get("draft") == 1
    assert counts.get("published") == 1
    assert first != second


def test_list_can_be_filtered_by_status(db: DB) -> None:
    first = db.save_post_to_queue(_make_post(post_id="1"))
    db.save_post_to_queue(_make_post(post_id="2"))
    db.update_queued_post(first, {"status": "published"})

    drafts = db.list_queued_posts(status="draft")
    assert [row["post_id"] for row in drafts] == ["2"]
    assert len(db.list_queued_posts()) == 2


def test_media_files_are_stored_as_json_list(db: DB) -> None:
    post = _make_post()
    post["media_files"] = json.dumps(["a.jpg", "b.jpg"])
    post["media_kind"] = "photo"
    post_id = db.save_post_to_queue(post)
    stored = db.get_queued_post(post_id)
    assert json.loads(stored["media_files"]) == ["a.jpg", "b.jpg"]
    assert stored["media_kind"] == "photo"


# ---------- Публикация из очереди ----------

class _FakeBot:
    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    async def send_message(self, **kwargs):
        if self.fail:
            raise self.fail
        self.sent.append({"kind": "message", **kwargs})

    async def send_photo(self, **kwargs):
        if self.fail:
            raise self.fail
        self.sent.append({"kind": "photo", **kwargs})

    async def send_video(self, **kwargs):
        if self.fail:
            raise self.fail
        self.sent.append({"kind": "video", **kwargs})

    async def send_document(self, **kwargs):
        if self.fail:
            raise self.fail
        self.sent.append({"kind": "document", **kwargs})

    async def send_media_group(self, **kwargs):
        if self.fail:
            raise self.fail
        self.sent.append({"kind": "media_group", **kwargs})


def test_queued_text_post_is_published(db: DB, monkeypatch) -> None:
    monkeypatch.setattr(main, "db", db)
    post_id = db.save_post_to_queue(_make_post())
    bot = _FakeBot()

    ok, message = __import__("asyncio").run(main.publish_queued_post(bot, db.get_queued_post(post_id)))

    assert ok is True
    assert "Опубликовано" in message
    assert len(bot.sent) == 1
    assert bot.sent[0]["kind"] == "message"
    assert bot.sent[0]["chat_id"] == "@target"
    assert bot.sent[0]["text"] == "Текст поста"


def test_post_without_target_channel_is_not_published(db: DB, monkeypatch) -> None:
    monkeypatch.setattr(main, "db", db)
    post_id = db.save_post_to_queue(_make_post(target=""))
    bot = _FakeBot()

    ok, message = __import__("asyncio").run(main.publish_queued_post(bot, db.get_queued_post(post_id)))

    assert ok is False
    assert "канал" in message.lower()
    assert bot.sent == []


def test_publish_error_is_reported(db: DB, monkeypatch) -> None:
    monkeypatch.setattr(main, "db", db)
    post_id = db.save_post_to_queue(_make_post())
    bot = _FakeBot(fail=RuntimeError("channel not found"))

    ok, message = __import__("asyncio").run(main.publish_queued_post(bot, db.get_queued_post(post_id)))

    assert ok is False
    assert "channel not found" in message


def test_due_scheduled_posts_are_published_by_the_parser(db: DB, monkeypatch) -> None:
    monkeypatch.setattr(main, "db", db)
    post_id = db.save_post_to_queue(_make_post())
    db.update_queued_post(
        post_id, {"status": "scheduled", "scheduled_at": "2000-01-01 00:00:00"}
    )
    bot = _FakeBot()

    published = __import__("asyncio").run(main._publish_due_scheduled_posts(bot))

    assert published == 1
    assert len(bot.sent) == 1
    assert db.get_queued_post(post_id)["status"] == "published"
