"""Проверка реального пути публикации: источник → AI-рерайт → маршрутизация
по теме → отправка получателю → фиксация публикации.

Тест не обращается к Telegram: вместо бота используется записывающий
double, а вместо облачного AI поднимается локальный mock-сервер с
OpenAI-совместимым контрактом. Проверяются настоящие HTTP-запрос и разбор
ответа, а также обработка ошибок авторизации и повторных попыток.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aiohttp
import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiohttp import web

import main
from database import DB


class MockAIServer:
    """Локальный OpenAI-совместимый сервер для проверки реального HTTP-пути."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.status = 200
        self.content = "ТЕМА: tech\nТЕКСТ: Переписанный текст новости.\nХЭШТЕГИ: #tech"
        self._runner: web.AppRunner | None = None
        self._port: int = 0

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self._port = site._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/v1/chat/completions"

    async def _handle(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self.requests.append(payload)
        if self.status != 200:
            return web.json_response(
                {"error": {"message": "mock failure", "type": "invalid_request_error"}},
                status=self.status,
            )
        return web.json_response(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": self.content}}
                ]
            }
        )


class RecordingBot:
    """Double вместо Telegram-бота: записывает отправки, ничего не отправляет."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.sent: list[dict] = []
        self.fail_with = fail_with

    async def _record(self, kind: str, kwargs: dict) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append({"kind": kind, **kwargs})

    async def send_message(self, **kwargs) -> None:
        await self._record("message", kwargs)

    async def send_photo(self, **kwargs) -> None:
        await self._record("photo", kwargs)

    async def send_video(self, **kwargs) -> None:
        await self._record("video", kwargs)

    async def send_document(self, **kwargs) -> None:
        await self._record("document", kwargs)

    async def send_media_group(self, **kwargs) -> None:
        await self._record("media_group", kwargs)


def source_message(msg_id: int = 101, text: str = "Исходный текст поста", media=None):
    return SimpleNamespace(id=msg_id, message=text, media=media)


def base_cfg(ai_url: str, topics: dict[str, str]) -> dict:
    return {
        "ads_words": [],
        "watermark": "",
        "topic_channels": topics,
        "ai_mode": "LOCAL",
        "local_url": ai_url,
        "local_model": "mock-model",
        "remote_url": "",
        "remote_model": "",
        "remote_api_key": "",
    }


async def publish_once(cfg: dict, bot: RecordingBot, db: DB, message=None):
    """Прогоняет один пост через реальный путь публикации."""
    main.db = db
    main.bot_running.set()
    try:
        async with aiohttp.ClientSession() as session:
            await main._process_message_group(
                client=None,
                source="@donor_channel",
                messages=[message or source_message()],
                cfg=cfg,
                bot=bot,
                ai_session=session,
            )
    finally:
        main.bot_running.clear()


@pytest.fixture
def db(tmp_path, monkeypatch):
    # Пауза между постами не относится к проверяемой логике.
    monkeypatch.setattr(main, "SLEEP_BETWEEN_POSTS_S", 0)
    database = DB(str(tmp_path / "publish.db"))
    yield database
    database.conn.close()


# ---------- Успешный путь ----------

def test_text_post_is_rewritten_routed_and_published(db: DB) -> None:
    async def scenario() -> tuple[MockAIServer, RecordingBot]:
        server = MockAIServer()
        await server.start()
        bot = RecordingBot()
        try:
            await publish_once(
                base_cfg(server.url, {"tech": "@tech_channel", "общее": "@fallback"}),
                bot,
                db,
            )
        finally:
            await server.stop()
        return server, bot

    server, bot = asyncio.run(scenario())

    # AI действительно получил запрос с корректной моделью и текстом поста.
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["model"] == "mock-model"
    assert request["messages"][-1]["content"] == "Исходный текст поста"
    assert request["temperature"] == main.AI_TEMPERATURE

    # Теги из ответа AI определили канал получателя.
    assert len(bot.sent) == 1
    assert bot.sent[0]["kind"] == "message"
    assert bot.sent[0]["chat_id"] == "@tech_channel"
    assert "Переписанный текст новости." in bot.sent[0]["text"]
    # Теги нужны только для маршрутизации — в самом посте их нет.
    assert "#tech" not in bot.sent[0]["text"]

    # Публикация зафиксирована, повторная отправка невозможна.
    assert db.is_published("@donor_channel", 101)
    logs = db.get_logs(limit=50)
    assert any(entry[1] == "OK-501" for entry in logs)


def test_unknown_topic_falls_back_to_common_channel(db: DB) -> None:
    async def scenario() -> RecordingBot:
        server = MockAIServer()
        server.content = "ТЕМА: неизвестная-тема\nТЕКСТ: Текст.\nХЭШТЕГИ: #x"
        await server.start()
        bot = RecordingBot()
        try:
            await publish_once(
                base_cfg(server.url, {"tech": "@tech_channel", "общее": "@fallback"}),
                bot,
                db,
            )
        finally:
            await server.stop()
        return bot

    bot = asyncio.run(scenario())
    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == "@fallback"


def test_watermark_is_appended_to_published_text(db: DB) -> None:
    async def scenario() -> RecordingBot:
        server = MockAIServer()
        await server.start()
        bot = RecordingBot()
        cfg = base_cfg(server.url, {"tech": "@tech_channel"})
        cfg["watermark"] = "Подписывайтесь на канал"
        try:
            await publish_once(cfg, bot, db)
        finally:
            await server.stop()
        return bot

    bot = asyncio.run(scenario())
    assert "Подписывайтесь на канал" in bot.sent[0]["text"]


# ---------- Ошибки AI ----------

@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_ai_error_leaves_post_unpublished_and_logged(db: DB, status: int) -> None:
    async def scenario() -> RecordingBot:
        server = MockAIServer()
        server.status = status
        await server.start()
        bot = RecordingBot()
        try:
            await publish_once(base_cfg(server.url, {"tech": "@tech_channel"}), bot, db)
        finally:
            await server.stop()
        return bot

    bot = asyncio.run(scenario())

    # Ничего не отправлено и публикация не зафиксирована — пост останется на повтор.
    assert bot.sent == []
    assert db.is_published("@donor_channel", 101) is False
    assert db.is_skipped("@donor_channel", 101) is False

    logs = db.get_logs(limit=50)
    types = {entry[1] for entry in logs}
    assert "ERR-403" in types or "ERR-400" in types or "ERR-402" in types
    # Текст ошибки не содержит содержимого ответа AI.
    assert any("ERR-40" in entry[1] for entry in logs)


def test_ai_unauthorized_response_is_reported_as_authorization_error(db: DB) -> None:
    """HTTP 401 от AI — это проблема доступа, а не формата ответа."""
    async def scenario() -> None:
        server = MockAIServer()
        server.status = 401
        await server.start()
        try:
            await publish_once(base_cfg(server.url, {"tech": "@tech_channel"}), RecordingBot(), db)
        finally:
            await server.stop()

    asyncio.run(scenario())
    logs = db.get_logs(limit=50)
    assert any(entry[1] in {"ERR-401", "ERR-402", "ERR-403", "ERR-400"} for entry in logs)


def test_malformed_ai_response_is_not_published(db: DB) -> None:
    async def scenario() -> RecordingBot:
        server = MockAIServer()
        server.content = "просто текст без структуры"
        await server.start()
        bot = RecordingBot()
        try:
            await publish_once(base_cfg(server.url, {"tech": "@tech_channel"}), bot, db)
        finally:
            await server.stop()
        return bot

    bot = asyncio.run(scenario())
    assert bot.sent == []
    assert db.is_published("@donor_channel", 101) is False
    logs = db.get_logs(limit=50)
    assert any(entry[1] == "WARN-404" for entry in logs)


def test_empty_ai_response_is_not_published(db: DB) -> None:
    async def scenario() -> RecordingBot:
        server = MockAIServer()
        server.content = ""
        await server.start()
        bot = RecordingBot()
        try:
            await publish_once(base_cfg(server.url, {"tech": "@tech_channel"}), bot, db)
        finally:
            await server.stop()
        return bot

    bot = asyncio.run(scenario())
    assert bot.sent == []
    assert db.is_published("@donor_channel", 101) is False


# ---------- Маршрутизация ----------

def test_missing_target_channel_keeps_post_retryable(db: DB) -> None:
    async def scenario() -> RecordingBot:
        server = MockAIServer()
        await server.start()
        bot = RecordingBot()
        try:
            # AI возвращает тему «tech», но маршрута для неё нет,
            # и запасной темы «общее» тоже нет.
            await publish_once(
                base_cfg(server.url, {"другая": "@other_channel"}), bot, db
            )
        finally:
            await server.stop()
        return bot

    bot = asyncio.run(scenario())
    assert bot.sent == []
    assert db.is_published("@donor_channel", 101) is False
    logs = db.get_logs(limit=50)
    assert any(entry[1] == "WARN-400" for entry in logs)


# ---------- Ошибки отправки ----------

def test_retryable_send_error_keeps_post_retryable(db: DB) -> None:
    async def scenario() -> tuple[RecordingBot, bool]:
        server = MockAIServer()
        await server.start()
        error = TelegramRetryAfter(
            method=SimpleNamespace(chat_id="@tech_channel", text="x"),
            message="too many requests",
            retry_after=1,
        )
        bot = RecordingBot(fail_with=error)
        try:
            await publish_once(base_cfg(server.url, {"tech": "@tech_channel"}), bot, db)
        finally:
            await server.stop()
        return bot, main.bot_running.is_set()

    bot, still_running = asyncio.run(scenario())
    assert bot.sent == []
    assert db.is_published("@donor_channel", 101) is False
    logs = db.get_logs(limit=50)
    assert any(entry[1] == "WARN-406" for entry in logs)
    assert still_running is False  # флаг сброшен в finally теста


# ---------- Фильтры ----------

def test_advertising_post_is_skipped_without_ai_call(db: DB) -> None:
    async def scenario() -> tuple[RecordingBot, MockAIServer]:
        server = MockAIServer()
        await server.start()
        bot = RecordingBot()
        cfg = base_cfg(server.url, {"tech": "@tech_channel"})
        cfg["ads_words"] = ["реклама", "спонсор"]
        try:
            await publish_once(
                cfg, bot, db, message=source_message(text="Это реклама канала")
            )
        finally:
            await server.stop()
        return bot, server

    bot, server = asyncio.run(scenario())
    assert bot.sent == []
    assert server.requests == []
    assert db.is_skipped("@donor_channel", 101) is True
    assert db.is_published("@donor_channel", 101) is False


def test_empty_post_is_skipped(db: DB) -> None:
    async def scenario() -> RecordingBot:
        server = MockAIServer()
        await server.start()
        bot = RecordingBot()
        try:
            await publish_once(
                base_cfg(server.url, {"tech": "@tech_channel"}),
                bot,
                db,
                message=source_message(text=""),
            )
        finally:
            await server.stop()
        return bot

    bot = asyncio.run(scenario())
    assert bot.sent == []
    assert db.is_skipped("@donor_channel", 101) is True
