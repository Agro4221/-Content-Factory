# Карта удаления: behavior, reviews, licensing

## АРТЕФАКТЫ ──────────────────────────────

### Директории (пакеты):
- `modules/behavior/` — 4 файла (`__init__.py`, `engine.py`, `session_runner.py`)
- `modules/reviews/` — 5 файлов (`__init__.py`, `engine.py`, `publisher.py`, `text_builder.py`)
- `licensing/` — 6 файлов (`__init__.py`, `client.py`, `offline_key.py`, `payment_gateway.py`)

### Одиночные файлы:
- `License/app.py` (сервер лицензий)
- `License/config.example.env`
- `License/config.py`
- `License/generate_offline_key.py`
- `License/license_server.db`
- `License/license_service.py`
- `License/license_store.py`
- `License/offline_key.py`

### Сопутствующие файлы:
- `.env.example`, `future_defaults.py` — настройки по умолчанию для behavior/reviews
- `generate_mj_key.py` — генерация лицензионных ключей (использует licensing)
- `tools/smoke_test.py` — тесты, проверяющие модули

## ВНЕШНИЕ ССЫЛКИ ────────────────────────

### [IMPORT] импорты:
| Файл | Строка | Тип | Описание |
|------|--------|-----|----------|
| `Auth.py` | 8-9 | IMPORT | `from licensing.client import get_license_client`, `generate_legacy_offline_key` |
| `config.py` | — | CONFIG | Константы `MODULE_BEHAVIOR`, `MODULE_REVIEWS` в SETTING_KEYS |
| `database.py` | 162+ | RUNTIME | Таблицы для behavior_sessions, reviews_accounts/queue/targets |
| `future_defaults.py` | 5-80 | CONFIG | BEHAVIOR_DEFAULTS, REVIEWS_DEFAULTS |
| `generate_mj_key.py` | 7-9 | IMPORT | Использование `licensing.offline_key.generate_signed_offline_key` |
| `main.py` | 44, 73 | RUNTIME | `check_license()`, `get_license_client()` для posts модуля |
| `Site.py` | 53-56, 102+ | IMPORT/TYPE_HINT | Импорт behavior_engine, reviews_engine, get_license_client/module_label |
| `startup.py` | 8-19 | IMPORT | Лицензионная проверка при старте через licensing.client |

### [RUNTIME] вызовы функций:
| Файл | Строка | Описание |
|------|--------|----------|
| `Site.py` | 403+ | Эндпоинты `/api/modules/*`, `/api/behavior/*`, `/api/reviews/*` |
| `Site.py` | 1245-1292 | `_build_behavior_stats()`, `_build_reviews_stats()` с runtime_snapshot |

### [TYPE_HINT] аннотации:
| Файл | Описание |
|------|----------|
| `Auth.py` | docstring упоминает модули posts/behavior/reviews |

## ГРАФ ЗАВИСИМОСТЕЙ ──────────────────────

```
┌──────────────────────────────────────────────────────────┐
│                    licensing                              │
│  (используется всеми: auth, main, startup, site)         │
└──────────────────────────────────────────────────────────┘
                        ↑
        behavior ───────┤       reviews ────────┤
      (behavior/engine.py)    (reviews/engine.py)

Зависимости между модулями:
  • reviews → licensing: ДА (через check_license для MODULE_REVIEWS)
  • behavior → licensing: ДА (через check_license для MODULE_BEHAVIOR)
  • behavior → reviews: НЕТ
```

## ПОРЯДОК УДАЛЕНИЯ ────────────────────────

1. **behavior** — не зависит от reviews, безопасно удалить первым
2. **reviews** — не зависит от behavior, можно после behavior
3. **licensing** — используется в auth.py и main.py для posts модуля → удалять последним!

## ФАЙЛЫ ДЛЯ УДАЛЕНИЯ (git rm) ────────────

```bash
# Модули
rm -r modules/behavior/
rm -r modules/reviews/
rm -r licensing/

# Сервер лицензий (отдельный каталог)
rm -r License/

# Файлы с импортами и вызовами:
git rm Auth.py          # если больше не нужен для posts-модуля
git rm startup.py       # содержит проверки лицензии
git rm Site.py          # API эндпоинты behavior/reviews
git rm generate_mj_key.py  # использует licensing

# Конфиги с настройками по умолчанию:
rm future_defaults.py   # можно удалить или адаптировать под posts только
```

## ФАЙЛЫ ДЛЯ РЕДАКТИРОВАНИЯ ────────────────

| Файл | Изменения |
|------|-----------|
| `Site.py` | Удалить импорты behavior_engine, reviews_engine; убрать эндпоинты /api/behavior/*, /api/reviews/*, /stats/behavior, /stats/reviews |
| `database.py` | Оставить таблицы (они могут содержать данные), но удалить методы для behavior_sessions и reviews_* |
| `config.py` | Удалить MODULE_BEHAVIOR, MODULE_REVIEWS из SETTING_KEYS; убрать константы модулей |
| `future_defaults.py` | Удалить BEHAVIOR_DEFAULTS, REVIEWS_DEFAULTS или адаптировать под posts |
| `main.py` | Оставить только check_license(MODULE_POSTS) — проверить что это работает для posts-модуля |
| `Auth.py` | Если используется только для posts → удалить импорты licensing; если нужен для posts — оставить как есть |

## ВЕРИФИКАЦИЯ ПОСЛЕ УДАЛЕНИЯ ────────────

```bash
# Линтер (проверка на unused imports)
ruff check . --select F401,F811,E999

# Установка без ошибок
pip install -e . --dry-run

# Тесты
pytest -x --tb=short -q

# Запуск приложения
python main.py  # должно работать только с posts-модулем
```
