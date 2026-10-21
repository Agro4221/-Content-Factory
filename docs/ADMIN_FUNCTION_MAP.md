# Карта функций админ-панели Content Factory

Снята перед редизайном. Редизайн не должен менять эти идентификаторы,
названия функций и API-вызовы.

## ID элементов (54)

### Статус (12)
`card-bot` `val-bot` `sub-bot` · `card-ai` `val-ai` `sub-ai` ·
`card-proxy` `val-proxy` `sub-proxy` · `card-stats` `val-stats` `sub-stats`

### Управление (3)
`btn-bot-toggle` `btn-bot-icon` `btn-bot-text`

### Контейнеры разделов (6)
`acc-quick` `acc-targets` `acc-ai` `acc-proxy` `acc-filters` `acc-logs`

### Поля Telegram (5)
`f-tg_api_id` `f-tg_api_hash` `f-bot_token` `f-sources` `f-poll_delay`

### Маршруты (2)
`topics-list` `add-topic`

### AI (8)
`f-ai_mode` `ai-remote-fields` `f-remote_url` `f-remote_model`
`f-remote_api_key` `ai-local-fields` `f-local_host` `f-local_port`
`f-local_model` `f-system_prompt`

### Прокси (8)
`f-use_proxy` `proxy-fields` `f-proxy_type` `f-proxy_host` `f-proxy_port`
`f-proxy_user` `f-proxy_pass` `f-mtproto_key` `proxy-result`

### Фильтры (2)
`f-ads_words` `f-watermark_text`

### Журнал (4)
`log-view` `clear-logs-btn` `log-hint` `clear-logs-confirm`

### Служебные (4)
`toast-container` + `data-secret-toggle` / `data-secret-clear` / `data-secret-hint`

## Функции JS (24)
`$id` `toast` `setCard` `refreshAll` `addTopicRow` `loadTopics` `collectTopics`
`shouldSkipField` `applySecretState` `setupSecretControls` `fillSettings`
`parseTopics` `saveQuick` `toggleBot` `testAI` `testProxy` `loadLogs`
`requestClearLogs` `cancelClearLogs` `confirmClearLogs` `toggleAIFields`
`toggleProxyFields` `escapeHtml`

## API-вызовы (9)
`/api/ai/models?provider=` · `/api/logs/clear` · `/api/logs?limit=30`
`/api/modules/posts/` · `/api/modules/status` · `/api/proxy/status`
`/api/proxy/test` · `/api/settings` · `/api/stats/posts`

## Inline-обработчики (10)
`cancelClearLogs()` `confirmClearLogs()` `loadLogs()` `refreshAll()`
`requestClearLogs()` `saveQuick()` `testAI()` `testProxy()`
`toggleAIFields()` `toggleBot()`

## Состояние JS
`editedFields` (Set) · `pendingSecretClear` (Set) · `forceSecretRefresh` (bool) ·
`SECRET_FIELDS` · `SECRET_MASK` · `SECRET_CLEAR_SIGNAL` · `API`

## Проверки, завязанные на структуру панели
- `tools/check_frontend.py` — набор CSS-правил, наличие всех ID из скрипта
- `tests/test_admin_startup_safety.cjs` — загрузка скрипта без исключений,
  соответствие ID разметке, маскирование секретов, адаптивность
- `tests/test_topic_channels_ui.cjs` — `parseTopics` / `collectTopics`
- `tests/test_proxy_connection_rejection.cjs` — `testProxy`
- `tests/test_admin_assets.py` — ключевые ID в разметке
