"""Playwright-тест админки Content Factory со стороны пользователя."""
import asyncio
import os
import re
import builtins
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8000"
SCREEN_DIR = "D:\\ContentFactory-updated\\_test_screenshots"

def _safe(s):
    if s is None:
        return ""
    s = str(s)
    s = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002700-\U000027BF\U0001F900-\U0001F9FF\U00002600-\U000026FF\ufe0f]', '', s)
    return s.encode('cp1251', errors='replace').decode('cp1251')

_original_print = builtins.print
def print(*args, **kwargs):
    args = tuple(_safe(a) if isinstance(a, str) else a for a in args)
    _original_print(*args, **kwargs)

async def open_accordion(page, index):
    accordions = await page.query_selector_all(".accordion")
    if index < len(accordions):
        head = await accordions[index].query_selector(".accordion-head")
        if head:
            await head.click()
            await page.wait_for_timeout(500)
            is_open = await accordions[index].evaluate("el => el.classList.contains('open')")
            return is_open
    return False

async def set_checkbox(page, selector, checked=True, toggle_fn=None):
    """Устанавливает чекбокс через evaluate, обходя перехват кликов."""
    toggle_call = f"; {toggle_fn}()" if toggle_fn else ""
    await page.evaluate(f"""() => {{
        const cb = document.querySelector('{selector}');
        if (cb) {{
            cb.checked = {str(checked).lower()};
            cb.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
        {toggle_call if toggle_fn else ""}
    }}""")
    await page.wait_for_timeout(500)

async def main():
    os.makedirs(SCREEN_DIR, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()
        # Ловим ошибки консоли браузера
        page_errors = []
        all_console = []
        page.on("console", lambda msg: all_console.append(f"[{msg.type}] {msg.text}"))
        page.on("console", lambda msg: page_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: page_errors.append(f"PageError: {exc}"))

        print("=" * 60)
        print("1. ОТКРЫТИЕ ГЛАВНОЙ СТРАНИЦЫ АДМИНКИ")
        print("=" * 60)
        await page.goto(BASE_URL + "/")
        await page.wait_for_timeout(2000)
        await page.screenshot(path=os.path.join(SCREEN_DIR, "01_main_desktop.png"), full_page=True)
        print("Скриншот: 01_main_desktop.png")

        title = await page.title()
        print(f"Заголовок страницы: {title}")

        cards = await page.query_selector_all(".card")
        print(f"Найдено карточек статуса: {len(cards)}")
        for i, card in enumerate(cards):
            h2 = await card.query_selector("h2")
            big = await card.query_selector(".big")
            if h2:
                h2_text = await h2.inner_text()
                big_text = await big.inner_text() if big else "—"
                print(f"  Карточка {i+1}: {h2_text} = {big_text}")

        print("\n" + "=" * 60)
        print("2. ПРОВЕРКА АККОРДЕОНОВ")
        print("=" * 60)
        accordions = await page.query_selector_all(".accordion")
        print(f"Найдено аккордеонов: {len(accordions)}")
        for i, acc in enumerate(accordions):
            head = await acc.query_selector(".accordion-head")
            if head:
                head_text = await head.inner_text()
                is_open = await acc.evaluate("el => el.classList.contains('open')")
                status = "ОТКРЫТ" if is_open else "закрыт"
                print(f"  [{i+1}] {status}: {head_text.strip()}")

        print("\n" + "=" * 60)
        print("3. ОТКРЫТИЕ ВСЕХ АККОРДЕОНОВ")
        print("=" * 60)
        for i in range(len(accordions)):
            is_open = await open_accordion(page, i)
            head_text = await (await accordions[i].query_selector(".accordion-head")).inner_text()
            print(f"  [{i+1}] после клика: {'открыт' if is_open else 'НЕ открыт'}: {head_text.strip()}")
        await page.screenshot(path=os.path.join(SCREEN_DIR, "02_all_accordions_open.png"), full_page=True)
        print("Скриншот: 02_all_accordions_open.png")

        print("\n" + "=" * 60)
        print("4. ЗАПОЛНЕНИЕ БЫСТРОГО СТАРТА (аккордеон 0)")
        print("=" * 60)
        await open_accordion(page, 0)
        await page.fill('input[id="f-tg_api_id"]', "123456")
        await page.fill('input[id="f-tg_api_hash"]', "test_hash_abcdef1234567890")
        await page.fill('input[id="f-bot_token"]', "123456:TEST_TOKEN_DO_NOT_USE_REAL")
        await page.fill('textarea[id="f-sources"]', "@mychannel1\n@mychannel2")
        await page.fill('input[id="f-poll_delay"]', "60")
        print("Поля быстрого старта заполнены")
        await page.screenshot(path=os.path.join(SCREEN_DIR, "03_quick_start_filled.png"), full_page=True)
        print("Скриншот: 03_quick_start_filled.png")

        print("\n" + "=" * 60)
        print("4b. СПИСОК ТЕМ (тема -> канал)")
        print("=" * 60)
        await open_accordion(page, 1)
        topic_rows = await page.query_selector_all('#topics-list .topic-row')
        print(f"Строк тем найдено: {len(topic_rows)}")
        if topic_rows:
            await page.fill('#topics-list .topic-row:nth-child(1) .topic-name', "общее")
            await page.fill('#topics-list .topic-row:nth-child(1) .topic-chat', "@my_target_channel")
        # Добавляем вторую тему кнопкой
        add_btn = await page.query_selector('#add-topic')
        if add_btn:
            await page.evaluate("() => { const b = document.querySelector('#add-topic'); if (b) b.click(); }")
            await page.wait_for_timeout(400)
            rows_after = await page.query_selector_all('#topics-list .topic-row')
            print(f"После клика «Добавить тему» строк: {len(rows_after)}")
            if len(rows_after) > 1:
                await page.fill('#topics-list .topic-row:nth-child(2) .topic-name', "tech")
                await page.fill('#topics-list .topic-row:nth-child(2) .topic-chat', "@my_tech_channel")
        await page.screenshot(path=os.path.join(SCREEN_DIR, "04_topics_list.png"), full_page=True)
        print("Скриншот: 04_topics_list.png")

        print("\n" + "=" * 60)
        print("5. НАСТРОЙКИ AI")
        print("=" * 60)
        await open_accordion(page, 2)
        await set_checkbox(page, 'input[id="f-ai_mode"]', True, toggle_fn="toggleAIFields")
        print("AI Mode переключён на удалённый (через evaluate)")
        await page.fill('input[id="f-remote_url"]', "https://api.openai.com/v1/chat/completions")
        await page.fill('input[id="f-remote_api_key"]', "sk-test1234567890abcdef")
        await page.fill('input[id="f-remote_model"]', "gpt-4o-mini")
        await page.screenshot(path=os.path.join(SCREEN_DIR, "04_ai_settings_filled.png"), full_page=True)
        print("Скриншот: 04_ai_settings_filled.png")

        print("\n" + "=" * 60)
        print("6. НАСТРОЙКИ ПРОКСИ")
        print("=" * 60)
        await open_accordion(page, 3)
        await set_checkbox(page, 'input[id="f-use_proxy"]', True, toggle_fn="toggleProxyFields")
        print("Прокси включён (через evaluate)")
        await page.fill('input[id="f-proxy_host"]', "proxy.test.example")
        await page.fill('input[id="f-proxy_port"]', "1080")
        await page.screenshot(path=os.path.join(SCREEN_DIR, "05_proxy_settings.png"), full_page=True)
        print("Скриншот: 05_proxy_settings.png")

        print("\n" + "=" * 60)
        print("7. ФИЛЬТРЫ И ПРАВИЛА")
        print("=" * 60)
        await open_accordion(page, 4)
        await page.fill('textarea[id="f-ads_words"]', "реклама\nспонсор\nподписывайтесь")
        await page.fill('input[id="f-watermark_text"]', "© Тестовый канал")
        await page.screenshot(path=os.path.join(SCREEN_DIR, "06_filters.png"), full_page=True)
        print("Скриншот: 06_filters.png")

        print("\n" + "=" * 60)
        print("8. ЖУРНАЛ СОБЫТИЙ")
        print("=" * 60)
        await open_accordion(page, 5)
        log_view = await page.query_selector('#log-view')
        log_text = await log_view.inner_text() if log_view else ""
        print(f"Журнал: {log_text[:200] if log_text else 'пусто'}")
        await page.screenshot(path=os.path.join(SCREEN_DIR, "07_logs_section.png"), full_page=True)
        print("Скриншот: 07_logs_section.png")

        print("\n" + "=" * 60)
        print("9. КНОПКА СОХРАНЕНИЯ")
        print("=" * 60)
        save_btn = await page.query_selector('button.btn-primary')
        if save_btn:
            btn_text = await save_btn.inner_text()
            print(f"Кнопка сохранения: {btn_text.strip()}")
            # Отладка: проверяем, доступна ли функция saveQuick
            fn_check = await page.evaluate("() => typeof saveQuick")
            print(f"saveQuick функция доступна: {fn_check}")
            # Вызываем saveQuick() напрямую
            result = await page.evaluate("() => { try { saveQuick(); return 'called'; } catch(e) { return 'error: ' + e.message; } }")
            print(f"saveQuick вызов: {result}")
            await page.wait_for_timeout(1200)
            toasts = await page.query_selector_all(".toast")
            print(f"Toast-уведомлений: {len(toasts)}")
            for t in toasts:
                t_text = await t.inner_text()
                print(f"  Toast: {t_text.strip()}")
        await page.screenshot(path=os.path.join(SCREEN_DIR, "08_after_save_click.png"), full_page=True)
        print("Скриншот: 08_after_save_click.png")

        print("\n" + "=" * 60)
        print("9b. ПРОВЕРКА СОХРАНЕНИЯ (повторная загрузка с сервера)")
        print("=" * 60)
        await page.reload()
        await page.wait_for_timeout(2500)
        saved = await page.evaluate("""() => {
            const get = (id) => { const e = document.getElementById(id); return e ? e.value : null; };
            const rows = Array.from(document.querySelectorAll('#topics-list .topic-row')).map(r => ({
                name: r.querySelector('.topic-name').value,
                chat: r.querySelector('.topic-chat').value
            }));
            return {
                tg_api_id: get('f-tg_api_id'),
                sources: get('f-sources'),
                poll_delay: get('f-poll_delay'),
                ai_remote: document.getElementById('f-ai_mode') ? document.getElementById('f-ai_mode').checked : null,
                proxy_type: get('f-proxy_type'),
                use_proxy: document.getElementById('f-use_proxy') ? document.getElementById('f-use_proxy').checked : null,
                ads_words: get('f-ads_words'),
                topics: rows
            };
        }""")
        print(f"  API ID: {saved['tg_api_id']}")
        print(f"  Источники: {str(saved['sources'])[:60]}")
        print(f"  Задержка: {saved['poll_delay']}")
        print(f"  AI удалённый: {saved['ai_remote']}")
        print(f"  Тип прокси: {saved['proxy_type']}")
        print(f"  Прокси включён: {saved['use_proxy']}")
        print(f"  Стоп-слова: {str(saved['ads_words'])[:40]}")
        print(f"  Темы: {saved['topics']}")

        print("\n" + "=" * 60)
        print("10. КНОПКА ЗАПУСКА ПАРСЕРА")
        print("=" * 60)
        start_btn = await page.query_selector('#btn-bot-toggle')
        if start_btn:
            btn_text = await start_btn.inner_text()
            btn_class = await start_btn.get_attribute("class")
            print(f"Кнопка запуска: {btn_text.strip()}, классы: {btn_class}")
            # Клик через evaluate
            await page.evaluate("() => { const b = document.querySelector('#btn-bot-toggle'); if (b) b.click(); }")
            await page.wait_for_timeout(1200)
            toasts = await page.query_selector_all(".toast")
            print(f"Toast-уведомлений: {len(toasts)}")
            for t in toasts[-3:]:
                t_text = await t.inner_text()
                print(f"  Toast: {t_text.strip()}")
            new_text = await start_btn.inner_text()
            new_class = await start_btn.get_attribute("class")
            print(f"После клика: {new_text.strip()}, классы: {new_class}")
        await page.screenshot(path=os.path.join(SCREEN_DIR, "09_after_start_click.png"), full_page=True)
        print("Скриншот: 09_after_start_click.png")

        print("\n" + "=" * 60)
        print("11. АДАПТИВНОСТЬ (375x812)")
        print("=" * 60)
        await context.close()
        context2 = await browser.new_context(viewport={"width": 375, "height": 812})
        page2 = await context2.new_page()
        await page2.goto(BASE_URL + "/")
        await page2.wait_for_timeout(2000)
        await page2.screenshot(path=os.path.join(SCREEN_DIR, "10_mobile_narrow.png"), full_page=True)
        print("Скриншот: 10_mobile_narrow.png")

        burger = await page2.query_selector('.topbar-burger')
        print(f"Burger-меню на мобильном: {'ЕСТЬ' if burger else 'НЕТ'}")

        cards_mobile = await page2.query_selector_all(".card")
        print(f"Карточек на мобильном: {len(cards_mobile)}")

        await context2.close()

        print("\n" + "=" * 60)
        print("12. ВСЕ СООБЩЕНИЯ КОНСОЛИ БРАУЗЕРА")
        print("=" * 60)
        if all_console:
            for e in all_console:
                print(f"  {_safe(e)}")
        else:
            print("  Сообщений консоли не зафиксировано")

        print("\n" + "=" * 60)
        print("13. ЗАКРЫТИЕ")
        print("=" * 60)
        await browser.close()

    print("\nГотово. Скриншоты в:", SCREEN_DIR)

asyncio.run(main())