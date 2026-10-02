# MaryJane Parser

> **RU:** Локальный Telegram-парсер и публикатор с FastAPI-панелью, AI-рерайтом, SQLite и поддержкой прокси. Проект создаётся в процессе обучения программированию, поэтому здесь честно показаны и готовые части, и незавершённые.
>
> **EN:** A local Telegram parser/publisher with a FastAPI admin panel, AI rewriting, SQLite storage and proxy support. I am building this project while learning software engineering, so the repository intentionally shows both finished parts and work in progress.

## 🚦 Status / Статус

**Working prototype / Рабочий прототип**

| Component | Status | Notes |
|---|---|---|
| Telegram parsing | ✅ | Reads recent messages from configured sources |
| Deduplication | ✅ | Persistent SQLite state |
| Ad filtering | ✅ | Configurable keyword filter |
| AI rewriting | ✅ | Local or remote OpenAI-compatible endpoint |
| Publishing | ✅ | Text and media via Telegram Bot API |
| FastAPI panel | ✅ | Settings, status, logs and proxy diagnostics |
| HTTP / SOCKS / MTProto | ✅ | Proxy checks and runtime support |
| Headless Telegram session | 🟡 | Requires a prepared session |
| Legacy licensing | 🟡 | Compatibility layer, not a complete licensing product |
| Payments | ❌ | Checkout is a stub |
| Production web auth | ❌ | Panel is intended for local use |
| CI release pipeline | ❌ | Build spec exists, release automation does not |

## 🧩 Architecture / Архитектура

~~~mermaid
flowchart LR
    TG["Telegram sources"] --> P["Telethon parser"]
    P --> F["Deduplication + ad filter"]
    F --> AI["AI rewrite"]
    AI --> T["Topic / text parser"]
    T --> B["aiogram Bot API"]
    B --> OUT["Target channel"]

    UI["FastAPI Admin"] --> DB["SQLite"]
    UI --> PX["Proxy diagnostics"]
    AI -. local / remote .-> LLM["OpenAI-compatible LLM"]
    P --> DB
    B --> DB
~~~

## 🎯 Goal / Цель

The project automates a small content pipeline:

**source → filter → AI rewrite → topic selection → publish → statistics**

The code grew from a practical experiment rather than from a finished framework.

## 🛠️ What is implemented / Что сделано

- asynchronous Telegram processing;
- duplicate protection;
- grouped media handling;
- configurable ad filtering;
- local and remote AI modes;
- structured AI response parsing;
- Telegram publishing;
- SQLite migrations and persistent settings;
- FastAPI settings/status/log API;
- HTTP, SOCKS4/5 and MTProto proxy support;
- PyInstaller build configuration;
- smoke-test tooling.

## 🧪 What is unfinished / Что не готово

Some parts are intentionally incomplete: automatic Telegram authorization, production authentication for the panel, a complete licensing backend, payment processing, release automation and several older experimental modules.

These are documented as gaps instead of being presented as completed functionality.

## 🧱 Tech stack / Стек

~~~text
Python 3.11+
FastAPI / Uvicorn
Telethon + aiogram
SQLite
OpenAI-compatible AI API
HTTP / SOCKS4 / SOCKS5 / MTProto
PyInstaller
~~~

## ▶️ Run / Запуск

~~~bash
python main.py
~~~

Local panel:

~~~text
http://127.0.0.1:8000/
~~~

Create .env from .env.example.

## 🧑‍💻 About the author / Об авторе

**RU:** Я пока новичок в разработке и учусь прямо на этих проектах. Постепенно разбираюсь с Python, API, базами данных, асинхронностью, архитектурой приложений, тестированием и инструментами разработки. Поэтому здесь могут встречаться неидеальные решения — это ещё и практический дневник моего роста.

**EN:** I am still a beginner developer and I am learning by building these projects. I am gradually working through Python, APIs, databases, async programming, application architecture, testing and development tooling. Some solutions may be imperfect, and the repository is also a record of that learning process.

## 📈 Roadmap / Дальше

~~~mermaid
flowchart LR
    A["Prototype"] --> B["Stabilization"]
    B --> C["Better tests"]
    C --> D["Authentication"]
    D --> E["CI / packaging"]
    E --> F["Stable release"]
~~~

## 📁 Structure / Структура

~~~text
main.py              # parser / publisher loop
Site.py              # FastAPI panel and API
database.py          # SQLite and migrations
config.py            # application configuration
env_loader.py        # environment loading
proxy_check.py       # proxy diagnostics
Auth.py              # legacy compatibility
Parser.spec          # PyInstaller configuration
tools/               # checks and utilities
admin.html            # admin UI
~~~

---

**RU:** Проект развивается вместе с моими навыками.

**EN:** The project grows together with my skills.
