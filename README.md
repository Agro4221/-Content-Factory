# 🏭 MaryJane Parser — Content Factory

> Локальный конвейер для сбора Telegram-контента, его фильтрации и AI-рерайта с последующей публикацией в целевые каналы.
>
> Проект вырос из практической задачи и постепенно превращается в более структурированный content pipeline с FastAPI-панелью, SQLite, прокси и несколькими режимами AI.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-panel-009688?logo=fastapi&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-Telethon%20%2B%20aiogram-26A5E4?logo=telegram&logoColor=white)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
![AI](https://img.shields.io/badge/AI-OpenAI--compatible-412991)
![Proxy](https://img.shields.io/badge/Proxy-HTTP%20%2F%20SOCKS%20%2F%20MTProto-555555)

> ⚠️ **Статус:** **Working Prototype / Рабочий прототип**
>
> Основной pipeline уже работает, но часть старых и инфраструктурных возможностей всё ещё находится в доработке. Проект не позиционируется как законченный production-сервис.

## ✨ Что умеет

| Возможность | Статус |
|---|---|
| 📥 Telegram parsing | ✅ |
| 🧹 Фильтрация рекламы | ✅ |
| ♻️ Защита от дублей | ✅ |
| 🤖 AI-рерайт | ✅ |
| 📝 Структурированный разбор результата AI | ✅ |
| 🖼️ Обработка и публикация медиа | ✅ |
| 📤 Публикация через Telegram Bot API | ✅ |
| 🖥️ FastAPI admin panel | ✅ |
| 🌐 HTTP / SOCKS4 / SOCKS5 / MTProto | ✅ |
| 💾 SQLite + миграции | ✅ |
| 🧰 PyInstaller build configuration | ✅ |
| 🔐 Headless Telegram session setup | 🟡 |
| 📜 Legacy licensing | 🟡 |
| 💳 Payments | ❌ |
| 🔑 Production web authentication | ❌ |
| 🚀 Automated release pipeline | ❌ |

## 🔄 Как работает pipeline

~~~text
Telegram sources
       │
       ▼
┌─────────────────┐
│    Telethon     │
│     parser      │
└────────┬────────┘
         ▼
┌────────────────────────┐
│ Deduplication +        │
│ advertisement filter   │
└────────┬───────────────┘
         ▼
┌─────────────────┐
│    AI rewrite   │
│ local / remote  │
└────────┬────────┘
         ▼
┌─────────────────┐
│ Topic / text    │
│ processing      │
└────────┬────────┘
         ▼
┌─────────────────┐
│ aiogram Bot API │
└────────┬────────┘
         ▼
 Target Telegram channel
~~~

## 🏗️ Архитектура

~~~mermaid
flowchart LR
    SRC["Telegram sources"] --> PARSER["Telethon parser"]
    PARSER --> FILTER["Deduplication + ad filter"]
    FILTER --> AI["AI rewrite"]
    AI --> PARSE["Topic / text parser"]
    PARSE --> PUB["aiogram publisher"]
    PUB --> OUT["Target channel"]

    PANEL["FastAPI Admin"] --> DB["SQLite"]
    PARSER --> DB
    PUB --> DB
    PANEL --> PROXY["Proxy diagnostics"]

    AI -. local / remote .-> LLM["OpenAI-compatible LLM"]
~~~

## 🎯 Цель проекта

Автоматизировать повторяющийся контентный процесс:

**источник → фильтрация → AI-рерайт → обработка → публикация**

При этом проект должен оставаться локальным и относительно простым в развёртывании.

## 🛠️ Реализовано

Сейчас в проекте есть асинхронная обработка Telegram, защита от повторной публикации, фильтрация рекламного контента, AI-рерайт в локальном или удалённом OpenAI-compatible endpoint, работа с текстом и медиа, SQLite-хранилище, FastAPI-панель и диагностика прокси.

Также присутствует конфигурация сборки через PyInstaller и набор служебных smoke-check инструментов.

## 🟡 Что ещё не готово

Не завершены полноценная автоматическая авторизация Telegram без заранее подготовленной сессии, production-аутентификация веб-панели, полноценный licensing/payment backend и автоматизированный release pipeline.

В репозитории также остаются отдельные старые/экспериментальные части, которые не являются обязательными для основного pipeline.

## ⚙️ Конфигурация

Локальная конфигурация задаётся через `.env`, созданный на основе `.env.example`.

Основные группы настроек:

~~~text
Telegram
AI / OpenAI-compatible endpoint
SQLite
Proxy
FastAPI
Publishing
Build / packaging
~~~

Реальные ключи, токены и локальные данные должны оставаться вне Git.

## 🚀 Быстрый старт

Установи зависимости:

~~~powershell
python -m pip install -r requirements.txt
~~~

Создай `.env` на основе `.env.example`, затем запусти:

~~~powershell
python main.py
~~~

Локальная панель:

~~~text
http://127.0.0.1:8000/
~~~

Для первого запуска лучше проверить pipeline на тестовом канале, а затем подключать реальные источники и публикацию.

## 🧪 Проверка

~~~text
Environment
    │
    ├─ configuration check
    ├─ database / migrations
    ├─ proxy diagnostics
    ├─ Telegram connectivity
    ├─ AI rewrite
    └─ publishing smoke test
~~~

Автоматические проверки не заменяют реальную авторизацию Telegram и проверку внешнего AI endpoint на конкретном компьютере.

## 🗺️ Roadmap

~~~mermaid
flowchart LR
    A["Working prototype"] --> B["Stabilization"]
    B --> C["Better test coverage"]
    C --> D["Authentication"]
    D --> E["Packaging / releases"]
    E --> F["Stable release"]
~~~

Главная задача roadmap — сделать основной pipeline предсказуемым, удобным для развёртывания и сопровождения.

## 👤 Об авторе / About the author

Я **Jostik (GitHub: [@Agro4221](https://github.com/Agro4221))**, начинающий разработчик, который учится через реальные проекты. Мне интересны **AI, нейростримеры, автоматизация, стриминг, Discord/Telegram и создание собственных инструментов**.

Сейчас я постепенно набираюсь опыта в Python, TypeScript, API, LLM, базах данных, асинхронности, аудио/видео и архитектуре приложений. Эти проекты — часть моего практического обучения: я стараюсь не просто повторять готовые примеры, а разбираться, как всё работает, и собирать собственные системы.

## 🔗 Ссылки / Links

- 💬 **Discord-сервер:** [присоединиться](https://discord.gg/ZK38tXnhhq) — сервер ещё находится в разработке.
- 📣 **Telegram:** [анонсы стримов и новости](https://t.me/+zb5pGLoWFbhhMTYy)
- 💜 **DonatePay:** [поддержать автора](https://new.donatepay.ru/@Jostik001)
- ❤️ **DonationAlerts:** [поддержать автора](https://www.donationalerts.com/r/i_jostik_i)

Поддержка не обязательна, но помогает продолжать разработку и эксперименты с проектами.

## 📁 Структура

~~~text
main.py              # основной parser / publisher loop
Site.py              # FastAPI panel и API
database.py          # SQLite и миграции
config.py            # конфигурация
env_loader.py        # загрузка окружения
proxy_check.py       # диагностика прокси
Auth.py              # legacy compatibility
Parser.spec          # PyInstaller configuration
tools/               # checks and utilities
admin.html            # admin UI
~~~

## 👤 Об авторе / About the author

Я **Jostik (GitHub: [@Agro4221](https://github.com/Agro4221))**, начинающий разработчик, который учится через реальные проекты. Мне интересны **AI, нейростримеры, автоматизация, стриминг, Discord/Telegram и создание собственных инструментов**.

Сейчас я постепенно набираюсь опыта в Python, TypeScript, API, LLM, базах данных, асинхронности, аудио/видео и архитектуре больших приложений. Эти репозитории — часть этого пути: я стараюсь не просто повторять готовые примеры, а разбираться, как всё работает, и собирать собственные системы.

## 🔗 Ссылки / Links

- 💬 **Discord-сервер:** [присоединиться](https://discord.gg/ZK38tXnhhq) — сервер ещё находится в разработке.
- 📣 **Telegram:** [анонсы стримов и новости](https://t.me/+zb5pGLoWFbhhMTYy)
- 💜 **DonatePay:** [поддержать автора](https://new.donatepay.ru/@Jostik001)
- ❤️ **DonationAlerts:** [поддержать автора](https://www.donationalerts.com/r/i_jostik_i)

Поддержка не обязательна, но помогает продолжать разработку и эксперименты с проектами.


---

**Jostik**