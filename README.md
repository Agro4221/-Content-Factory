# MaryJane Parser v1.28.3.6

Коммерческая сборка на базе Project MaryJane (v4): Telegram-парсер/публикатор, локальная админка FastAPI, модульная лицензия.

**Исходники в `Project Parser + Modules` не изменялись** — это отдельный продукт в папке `Parser v1.28.3.6`.

## Модули

| ID | Название | Описание |
|----|----------|----------|
| `posts` | Публикации | Парсинг каналов, AI, публикация в Telegram |
| `behavior` | Поведенческий фактор | **MVP (симуляция):** имитация визитов на URL из админки; реальный браузерный трафик — позже |
| `reviews` | Отзывы | **MVP (симуляция):** очередь и имитация публикации; интеграция с площадками — позже |

В API `/api/stats/behavior` и `/api/stats/reviews` поле `simulation_mode: true` и пояснение в `mode_note`.

## Лицензирование

1. **Legacy offline** — 24-символьный hex-ключ (все модули, без срока).
2. **MJ-v1 offline** — подписанный ключ `MJ-v1.{payload}.{sig}` с HWID, списком модулей и сроком.
3. **Online** — активация кодом `MJON-...` через сервер, токен + периодическая валидация.

Проверка модулей: без лицензии на `behavior` / `reviews` соответствующие API и настройки блокируются (403).

### Переменные окружения (клиент)

```env
LICENSE_SERVER_URL=https://your-license-server.example.com
LICENSE_SIGNING_SECRET=your-shared-secret
LEGACY_LICENSE_SALT=Jostik_Premium_AI_Aggregator_2026
LICENSE_ONLINE_REFRESH_SECONDS=3600
```

## Конфигурация (.env)

Все ключи и секреты — в **`.env`** рядом с приложением (не в git).

При первом запуске файл создаётся из `.env.example`. Шаблон:

- API, браузер при старте (`BROWSER_OPEN_ON_START`)
- лицензия (`LICENSE_SERVER_URL`, `LICENSE_SIGNING_SECRET`, `LICENSE_VERIFY_ON_STARTUP`)
- опционально Telegram / AI / прокси (дублируют админку при пустой БД)

## Запуск (Python)

```bash
cd "Parser v1.28.3.6"
python main.py
```

При первом запуске `deps.py` сам создаст `.venv`, установит зависимости из `requirements.txt` и перезапустит процесс.

Админка: http://127.0.0.1:8000/ (редирект на `/activate` без лицензии)  
Активация: http://127.0.0.1:8000/activate

## Сборка .exe

```powershell
.\build\build_exe.ps1
```

Подробности: **`BUILD.md`**. После запуска `dist\MaryJane.exe` админка открывается в браузере автоматически.

## Проверка перед релизом

```powershell
.venv\Scripts\python tools\smoke_test.py
```

## Сервер лицензий

Каталог **`Server files/`** — отдельное FastAPI-приложение для VPS:

- `/api/v1/activate`, `/api/v1/validate`
- каталог тарифов с ограничением по модулям
- заготовка оплаты (`/api/v1/payments/checkout`, webhook-заглушка)

См. `Server files/README.md`.

## Структура

```
Parser v1.28.3.6/
├── main.py              # цикл бота (модуль posts)
├── Site.py              # FastAPI + админ API
├── admin.html           # UI (в т.ч. behavior / reviews)
├── licensing/           # клиент лицензий
├── modules/
│   ├── behavior/engine.py
│   └── reviews/engine.py
├── integrations/        # license refresh, payment checkout proxy
├── startup.py           # каталоги, .env→БД, проверка лицензии при старте
├── paths.py / env_loader.py
├── .env.example         # шаблон секретов (копируется в .env)
├── build/               # PyInstaller spec + build_exe.ps1
├── tools/smoke_test.py  # проверка модулей перед сборкой
└── Server files/        # сервер для продакшена
```

## Платежи (заглушка)

Клиент: `POST /api/payments/checkout` → прокси на сервер.  
Сервер: тестовая страница `/pay/stub/{session_id}`.  
Для Stripe / YooKassa / Robokassa замените webhook на сервере.

## Версия

`1.28.3.6` — см. `VERSION.txt`, `config.APP_VERSION`.

## Что уточнить у заказчика

- Провайдер оплаты (Stripe, YooKassa, Robokassa).
- Модель по умолчанию: гибрид offline+online или только online для новых ключей.
- SKU/цены и привязка модулей к тарифам на витрине.
