# MaryJane Parser

Локальный Telegram-парсер/публикатор с веб-панелью на FastAPI, SQLite-хранилищем, AI-рерайтом и поддержкой прокси.

> **Статус:** рабочий прототип / неполный публичный snapshot. Runtime-данные и секреты намеренно не входят в репозиторий.

## Что работает

| Компонент | Состояние | Примечание |
|---|---|---|
| Telegram parsing | ✅ | Telethon, последние 10 сообщений источника за цикл |
| Дедупликация | ✅ | SQLite, ключ source:message_id |
| Фильтр рекламы | ✅ | Настраиваемый список слов |
| AI rewrite | ✅ | Локальный или удалённый OpenAI-compatible endpoint |
| Публикация текста/медиа | ✅ | Telegram Bot API через aiogram |
| FastAPI admin UI | ✅ | Настройки, статус, логи, диагностика прокси |
| HTTP/SOCKS/MTProto proxy | ✅ | Проверка и подключение реализованы |
| Headless Telegram session | ⚠️ | Нужен заранее созданный .session |
| Licensing | ⚠️ | Legacy compatibility layer; полноценного сервера в snapshot нет |
| Payments | ❌ | Checkout оставлен заглушкой |
| Behavior / Reviews | ❌ | Эти модули удалены из текущего snapshot |
| Web authentication | ❌ | Панель рассчитана на локальный запуск |
| CI / release pipeline | ❌ | В репозитории есть только PyInstaller spec |

## Архитектура

~~~text
Telegram channels
      │
      ▼
   Telethon ──► parser / dedupe / ad filter
                     │
                     ▼
                AI rewrite
            ┌────────┴────────┐
            │                 │
      local endpoint     remote endpoint
            │                 │
            └────────┬────────┘
                     ▼
              topic / text parser
                     │
                     ▼
               aiogram Bot API
                     │
                     ▼
             target Telegram channel

FastAPI ──► settings / status / logs / proxy test
SQLite  ──► runtime state / logs / settings
~~~

## Требования

- Python 3.11+
- Telegram API credentials: TG_API_ID, TG_API_HASH
- Telegram bot token: BOT_TOKEN
- зависимости из requirements.txt
- AI endpoint: локальный или удалённый OpenAI-compatible API

## Запуск

~~~bash
python main.py
~~~

По умолчанию панель: http://127.0.0.1:8000/

Перед запуском создайте .env по образцу .env.example.

## Конфигурация и безопасность

Реальные секреты не хранятся в Git. В .env используются Telegram, AI, proxy и legacy licensing settings.

Настройки system_prompt, источников, целевого канала и остальные runtime-настройки сохраняются локально в SQLite.

Панель по умолчанию слушает 127.0.0.1 и не имеет полноценной веб-аутентификации. Не открывайте её напрямую в интернет.

## Что исключено из публичного Git

- .env
- Telegram session files
- *.db и WAL/SHM-файлы
- __pycache__ и *.pyc
- build/, dist/
- *.toc, *.pyz, временные ZIP-архивы
- PyInstaller diagnostics
- локальные логи и временные медиа

## Сборка

~~~bash
python -m PyInstaller Parser.spec --clean
~~~

Parser.spec — конфигурация PyInstaller. Полноценного installer/CI-release pipeline в текущем snapshot нет.

## Известные ограничения

Это исходный прототип, а не production-ready релиз. Внешние Telegram/AI сервисы не входят в тестовый стенд репозитория, поэтому их доступность и совместимость зависят от окружения.

Публичная версия намеренно не содержит runtime-базы, Telegram-сессии и секреты пользователя.

## Структура

~~~text
main.py              # основной цикл парсера/публикатора
Site.py              # FastAPI-панель и API
database.py          # SQLite и миграции
config.py            # конфигурация
env_loader.py        # загрузка .env
proxy_check.py       # диагностика прокси
Auth.py              # legacy compatibility layer
Parser.spec          # PyInstaller spec
tools/               # smoke/служебные скрипты
admin.html           # локальный интерфейс
activate.html        # legacy compatibility page
~~~
