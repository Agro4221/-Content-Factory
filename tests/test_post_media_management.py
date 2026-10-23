"""Медиа поста в очереди: прикрепление и удаление из панели.

Пользователь должен уметь поправить пост вручную: убрать лишний файл или
прикрепить свой. Файл приходит телом запроса — без дополнительных
библиотек для multipart.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import Site

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class FakeDB:
    def __init__(self) -> None:
        self.posts: dict[int, dict] = {
            1: {
                "id": 1,
                "source_name": "@donor",
                "post_id": "42",
                "group_id": "",
                "original_text": "Исходник",
                "publish_text": "Черновик",
                "media_files": "[]",
                "media_kind": "",
                "category": "новости",
                "target_chat": "@target",
                "target_chats": "[]",
                "status": "draft",
                "scheduled_at": None,
                "published_at": None,
                "last_error": "",
                "created_at": "2026-01-01 00:00:00",
                "updated_at": "2026-01-01 00:00:00",
            }
        }
        self.logs: list[tuple[str, str]] = []

    def add_log(self, log_type: str, message: str) -> None:
        self.logs.append((log_type, message))

    def get_queued_post(self, post_id: int) -> dict | None:
        return self.posts.get(int(post_id))

    def update_queued_post(self, post_id: int, changes: dict) -> bool:
        row = self.posts.get(int(post_id))
        if not row:
            return False
        row.update(changes)
        return True


@pytest.fixture
def client(monkeypatch, tmp_path):
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)
    monkeypatch.setattr(Site, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(Site, "POST_MEDIA_DIR", "queue_media")
    with TestClient(
        Site.app,
        client=("127.0.0.1", 12345),
        raise_server_exceptions=False,
    ) as test_client:
        test_client.fake_db = fake_db
        yield test_client


def test_media_can_be_attached(client: TestClient) -> None:
    response = client.post(
        "/api/posts/1/media?name=photo.png",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    files = json.loads(client.fake_db.posts[1]["media_files"])
    assert len(files) == 1
    # В имени файла есть метка поста — так медиа не перепутать.
    assert "donor" in files[0] or files[0].startswith("p")


def test_uploaded_file_is_written_to_disk(client: TestClient, tmp_path) -> None:
    response = client.post(
        "/api/posts/1/media?name=photo.png",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 200
    files = json.loads(client.fake_db.posts[1]["media_files"])
    saved = tmp_path / "queue_media" / files[0]
    assert saved.is_file()
    assert saved.read_bytes() == PNG_BYTES


def test_disallowed_extension_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/posts/1/media?name=payload.exe",
        content=b"MZ",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400
    assert json.loads(client.fake_db.posts[1]["media_files"]) == []


def test_path_traversal_in_name_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/posts/1/media?name=../../escape.png",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 400


def test_empty_file_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/posts/1/media?name=photo.png",
        content=b"",
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 400


def test_media_can_be_removed(client: TestClient) -> None:
    client.post(
        "/api/posts/1/media?name=photo.png",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    files = json.loads(client.fake_db.posts[1]["media_files"])
    assert len(files) == 1

    response = client.delete("/api/posts/1/media/0")
    assert response.status_code == 200, response.text
    assert json.loads(client.fake_db.posts[1]["media_files"]) == []


def test_removed_file_is_deleted_from_disk(client: TestClient, tmp_path) -> None:
    client.post(
        "/api/posts/1/media?name=photo.png",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    files = json.loads(client.fake_db.posts[1]["media_files"])
    saved = tmp_path / "queue_media" / files[0]
    assert saved.is_file()

    client.delete("/api/posts/1/media/0")
    assert not saved.exists()


def test_removing_out_of_range_index_is_rejected(client: TestClient) -> None:
    response = client.delete("/api/posts/1/media/5")
    assert response.status_code == 404


def test_media_of_missing_post_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/posts/999/media?name=photo.png",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 404


def test_published_post_media_is_readonly(client: TestClient) -> None:
    client.fake_db.posts[1]["status"] = "published"
    response = client.post(
        "/api/posts/1/media?name=photo.png",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 409
