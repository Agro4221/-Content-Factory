"""Проверяет обработку постов, которые не удаётся обработать.

Количество попыток ограничено: после повторных неудач запись помечается
как пропущенная, чтобы не блокировать обработку следующих постов источника.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import main


class FakeDB:
    def __init__(self) -> None:
        self.skipped: list[tuple[str, list[int], str]] = []
        self.published: list[tuple[str, int]] = []
        self.logs: list[tuple[str, str]] = []
        self.settings: dict[str, str] = {}

    def add_log(self, log_type: str, message: str) -> None:
        self.logs.append((log_type, message))

    def get_setting(self, key: str, default: str = "") -> str:
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value

    def save_skipped_posts(self, source: str, post_ids: list[int], reason: str) -> None:
        self.skipped.append((source, list(post_ids), reason))

    def save_skipped_post(self, source: str, post_id: int, reason: str) -> None:
        self.skipped.append((source, [post_id], reason))

    def save_posts(self, source: str, post_ids: list[int]) -> None:
        for post_id in post_ids:
            self.published.append((source, post_id))

    def save_post(self, source: str, post_id: int) -> None:
        self.published.append((source, post_id))

    def is_published(self, source: str, post_id: int) -> bool:
        return (source, post_id) in self.published

    def is_skipped(self, source: str, post_id: int) -> bool:
        return any(
            entry_source == source and post_id in post_ids
            for entry_source, post_ids, _reason in self.skipped
        )

    def get_all_settings(self) -> dict[str, str]:
        return dict(self.settings)


class FakeClient:
    """Клиент, который отдаёт один и тот же пост при каждом опросе."""

    def __init__(self, messages: list) -> None:
        self._messages = messages

    def iter_messages(self, *_args, **_kwargs):
        async def generator():
            for message in self._messages:
                yield message

        return generator()


class Running:
    @staticmethod
    def is_set() -> bool:
        return True


def _setup(monkeypatch, db: FakeDB) -> None:
    async def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "bot_running", Running())
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)
    main._POISON_ATTEMPTS.clear()


def _cfg() -> dict:
    return {
        "ads_words": [],
        "watermark": "",
        "publish_mode": "auto",
        "tag_routes": {"@target": ["новости"]},
        "tag_routes_tags": ["новости"],
        "poll_delay": 60,
    }


def test_broken_post_is_skipped_after_several_attempts(monkeypatch) -> None:
    """После нескольких неудач пост пропускается, и лента идёт дальше."""
    db = FakeDB()
    _setup(monkeypatch, db)

    async def failing_group(*_args, **_kwargs):
        return

    monkeypatch.setattr(main, "_process_message_group", failing_group)

    message = SimpleNamespace(id=7, message="текст", media=None, grouped_id=None)
    client = FakeClient([message])

    for _attempt in range(main.MAX_POST_ATTEMPTS):
        asyncio.run(main._process_one_source(client, "@source", _cfg(), None, None))

    # Пост помечен пропущенным — он больше не блокирует источник.
    assert db.skipped
    source, post_ids, reason = db.skipped[-1]
    assert source == "@source"
    assert post_ids == [7]
    assert reason == "retries_exhausted"


def test_broken_post_is_not_skipped_immediately(monkeypatch) -> None:
    """Первая неудача — обычный повтор, а не пропуск поста."""
    db = FakeDB()
    _setup(monkeypatch, db)

    async def failing_group(*_args, **_kwargs):
        return

    monkeypatch.setattr(main, "_process_message_group", failing_group)

    message = SimpleNamespace(id=7, message="текст", media=None, grouped_id=None)
    client = FakeClient([message])

    asyncio.run(main._process_one_source(client, "@source", _cfg(), None, None))

    assert db.skipped == []


def test_attempt_counter_resets_for_healthy_posts(monkeypatch) -> None:
    """Успешно обработанный пост не оставляет счётчик попыток."""
    db = FakeDB()
    _setup(monkeypatch, db)

    async def failing_group(*_args, **_kwargs):
        return

    monkeypatch.setattr(main, "_process_message_group", failing_group)
    message = SimpleNamespace(id=7, message="текст", media=None, grouped_id=None)
    client = FakeClient([message])

    asyncio.run(main._process_one_source(client, "@source", _cfg(), None, None))
    assert main._POISON_ATTEMPTS

    # Пост «починился»: теперь он обрабатывается и считается опубликованным.
    async def working_group(*_args, **_kwargs):
        db.save_posts("@source", [7])

    monkeypatch.setattr(main, "_process_message_group", working_group)
    asyncio.run(main._process_one_source(client, "@source", _cfg(), None, None))

    assert main._POISON_ATTEMPTS == {}
