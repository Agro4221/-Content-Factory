"""API очереди постов и локальных провайдеров AI."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import Site
from config import DEFAULT_LOCAL_AI_PROVIDER, LOCAL_AI_PROVIDERS


class FakeDB:
    def __init__(self) -> None:
        self.posts: dict[int, dict] = {}
        self.next_id = 1
        self.settings: dict[str, str] = {"bot_token": ""}

    # --- очередь постов ---
    def save_post_to_queue(self, post: dict) -> int:
        post_id = self.next_id
        self.next_id += 1
        row = {
            "id": post_id,
            "source_name": post.get("source_name", ""),
            "post_id": post.get("post_id", ""),
            "group_id": post.get("group_id", ""),
            "original_text": post.get("original_text", ""),
            "publish_text": post.get("publish_text", ""),
            "media_files": post.get("media_files", "[]"),
            "media_kind": post.get("media_kind", ""),
            "category": post.get("category", ""),
            "target_chat": post.get("target_chat", ""),
            "status": "draft",
            "scheduled_at": None,
            "published_at": None,
            "last_error": "",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }
        self.posts[post_id] = row
        return post_id

    def list_queued_posts(self, status: str = "", limit: int = 200) -> list[dict]:
        rows = list(self.posts.values())
        if status:
            rows = [r for r in rows if r["status"] == status]
        return rows[:limit]

    def get_queued_post(self, post_id: int) -> dict | None:
        return self.posts.get(int(post_id))

    def update_queued_post(self, post_id: int, changes: dict) -> bool:
        row = self.posts.get(int(post_id))
        if not row:
            return False
        row.update(changes)
        return True

    def delete_queued_post(self, post_id: int) -> bool:
        return self.posts.pop(int(post_id), None) is not None

    def due_scheduled_posts(self, limit: int = 20) -> list[dict]:
        return []

    def count_queued_posts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.posts.values():
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts

    # --- настройки ---
    def get_all_settings(self) -> dict[str, str]:
        return dict(self.settings)

    def get_masked_settings(self) -> dict[str, str]:
        return dict(self.settings)

    def set_settings(self, settings: dict[str, str]) -> None:
        self.settings.update(settings)


@pytest.fixture
def client(monkeypatch):
    fake_db = FakeDB()
    monkeypatch.setattr(Site, "db", fake_db)
    with TestClient(
        Site.app,
        client=("127.0.0.1", 12345),
        raise_server_exceptions=False,
    ) as test_client:
        test_client.fake_db = fake_db
        yield test_client


# ---------- Очередь постов ----------

def test_empty_queue_returns_empty_list(client: TestClient) -> None:
    response = client.get("/api/posts")
    assert response.status_code == 200
    payload = response.json()
    assert payload["posts"] == []
    assert payload["total"] == 0
    assert "draft" in payload["counts"]


def test_post_appears_in_queue(client: TestClient) -> None:
    client.fake_db.save_post_to_queue(
        {
            "source_name": "@donor",
            "post_id": "42",
            "original_text": "Исходник",
            "publish_text": "Готовый текст",
            "media_files": "[]",
            "category": "tech",
            "target_chat": "@target",
        }
    )
    response = client.get("/api/posts")
    assert response.status_code == 200
    posts = response.json()["posts"]
    assert len(posts) == 1
    assert posts[0]["publish_text"] == "Готовый текст"
    assert posts[0]["status"] == "draft"
    assert posts[0]["media_count"] == 0


def test_post_can_be_edited_through_api(client: TestClient) -> None:
    post_id = client.fake_db.save_post_to_queue(
        {"source_name": "@donor", "post_id": "1", "publish_text": "Было", "media_files": "[]"}
    )
    response = client.post(
        f"/api/posts/{post_id}",
        json={"publish_text": "Стало", "category": "TECH", "target_chat": "@new"},
    )
    assert response.status_code == 200
    stored = client.fake_db.get_queued_post(post_id)
    assert stored["publish_text"] == "Стало"
    assert stored["category"] == "tech"  # приводится к нижнему регистру
    assert stored["target_chat"] == "@new"


def test_scheduling_via_api_sets_status(client: TestClient) -> None:
    post_id = client.fake_db.save_post_to_queue(
        {"source_name": "@donor", "post_id": "1", "media_files": "[]"}
    )
    response = client.post(
        f"/api/posts/{post_id}", json={"scheduled_at": "2026-12-31 23:00:00"}
    )
    assert response.status_code == 200
    stored = client.fake_db.get_queued_post(post_id)
    assert stored["status"] == "scheduled"
    assert stored["scheduled_at"] == "2026-12-31 23:00:00"


def test_post_can_be_cancelled(client: TestClient) -> None:
    post_id = client.fake_db.save_post_to_queue(
        {"source_name": "@donor", "post_id": "1", "media_files": "[]"}
    )
    response = client.post(f"/api/posts/{post_id}/cancel")
    assert response.status_code == 200
    assert client.fake_db.get_queued_post(post_id)["status"] == "cancelled"


def test_post_can_be_deleted(client: TestClient) -> None:
    post_id = client.fake_db.save_post_to_queue(
        {"source_name": "@donor", "post_id": "1", "media_files": "[]"}
    )
    response = client.post(f"/api/posts/{post_id}/delete")
    assert response.status_code == 200
    assert client.fake_db.get_queued_post(post_id) is None


def test_missing_post_returns_404(client: TestClient) -> None:
    assert client.get("/api/posts/999").status_code == 404
    assert client.post("/api/posts/999/cancel").status_code == 404
    assert client.post("/api/posts/999/delete").status_code == 404


def test_publishing_without_token_is_rejected(client: TestClient) -> None:
    post_id = client.fake_db.save_post_to_queue(
        {"source_name": "@donor", "post_id": "1", "media_files": "[]", "target_chat": "@target"}
    )
    response = client.post(f"/api/posts/{post_id}/publish")
    assert response.status_code == 400
    assert "Bot Token" in response.json()["detail"]


def test_unknown_status_is_rejected(client: TestClient) -> None:
    response = client.get("/api/posts?status=unknown")
    assert response.status_code == 400


def test_queue_can_be_filtered_by_status(client: TestClient) -> None:
    first = client.fake_db.save_post_to_queue({"source_name": "@d", "post_id": "1", "media_files": "[]"})
    client.fake_db.save_post_to_queue({"source_name": "@d", "post_id": "2", "media_files": "[]"})
    client.fake_db.update_queued_post(first, {"status": "published"})

    published = client.get("/api/posts?status=published").json()
    assert [p["post_id"] for p in published["posts"]] == ["1"]

    drafts = client.get("/api/posts?status=draft").json()
    assert [p["post_id"] for p in drafts["posts"]] == ["2"]


# ---------- Провайдеры AI ----------

def test_providers_endpoint_lists_all_providers(client: TestClient) -> None:
    response = client.get("/api/ai/providers")
    assert response.status_code == 200
    payload = response.json()
    ids = [p["id"] for p in payload["providers"]]
    assert set(ids) == set(LOCAL_AI_PROVIDERS)
    assert payload["default"] == DEFAULT_LOCAL_AI_PROVIDER


def test_each_provider_has_loopback_host_and_port(client: TestClient) -> None:
    payload = client.get("/api/ai/providers").json()
    for provider in payload["providers"]:
        assert provider["host"] == "127.0.0.1", provider["id"]
        assert 1 <= int(provider["port"]) <= 65535, provider["id"]
        assert provider["label"], provider["id"]


def test_expected_default_ports(client: TestClient) -> None:
    payload = client.get("/api/ai/providers").json()
    ports = {p["id"]: p["port"] for p in payload["providers"]}
    assert ports["lmstudio"] == 1234
    assert ports["ollama"] == 11434
    assert ports["anythingllm"] == 3001


def test_unknown_provider_is_rejected(client: TestClient) -> None:
    response = client.get("/api/ai/models?provider=does-not-exist")
    assert response.status_code == 400


def test_non_loopback_host_is_refused(client: TestClient) -> None:
    """Список моделей не должен превращаться в общий серверный запрос."""
    response = client.get("/api/ai/models?provider=ollama&host=example.com")
    assert response.status_code == 400
    assert "локальн" in response.json()["detail"].lower()
