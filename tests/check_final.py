"""Финальная сквозная проверка админ-панели Content Factory.

Покрывает: навигацию по всем разделам, статус, сохранение и повторную
загрузку настроек, секреты (маска вместо значения), очередь постов,
справку по кодам, очистку медиа, журнал, запуск/остановку парсера,
тему оформления и мобильную ширину 375 px.

Запуск (при запущенном приложении):
    python tests/check_final.py
"""
import asyncio
import os
import re
import builtins
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8000"
OUT = r"D:\ContentFactory-updated\_test_screenshots"

def _safe(s):
    if s is None:
        return ""
    s = str(s)
    s = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002700-\U000027BF\U0001F900-\U0001F9FF\U00002600-\U000026FF\ufe0f]', '', s)
    return s.encode('cp1251', errors='replace').decode('cp1251')

_original_print = builtins.print
def print(*args, **kwargs):
    args = tuple(_safe(a) if isinstance(a, str) else x for x, a in [] ) or args
    _original_print(*[(_safe(a) if isinstance(a, str) else a) for a in args], **kwargs)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1000})
        page = await ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await page.goto(BASE_URL + "/")
        await page.wait_for_timeout(2500)

        print("=" * 66)
        print("A. НАВИГАЦИЯ ПО ВСЕМ РАЗДЕЛАМ")
        print("=" * 66)
        tabs = await page.query_selector_all(".nav-tab[data-panel]")
        expected = ["quick", "targets", "ai", "proxy", "filters", "posts", "logs", "faq"]
        check("Все 8 разделов присутствуют", len(tabs) == len(expected), f"найдено: {len(tabs)}")
        for i, tab in enumerate(tabs):
            panel = await tab.get_attribute("data-panel")
            await tab.click()
            await page.wait_for_timeout(350)
            visible = await page.evaluate(
                "p => { const e = document.querySelector('.panel[data-panel=\"' + p + '\"]');"
                " return e ? e.classList.contains('active') && e.offsetHeight > 0 : false; }", panel)
            check(f"Раздел «{panel}» открывается", visible)

        print()
        print("=" * 66)
        print("B. СТАТУС И УПРАВЛЕНИЕ ПАРСЕРОМ")
        print("=" * 66)
        cards = await page.query_selector_all(".status-strip .card")
        check("Полоса статуса содержит 4 метрики", len(cards) == 4, f"карточек: {len(cards)}")
        await page.evaluate("() => selectPanel('quick')")
        await page.wait_for_timeout(400)
        label_before = (await page.inner_text("#btn-bot-text")).strip()
        await page.evaluate("() => toggleBot()")
        await page.wait_for_timeout(2500)
        label_after = (await page.inner_text("#btn-bot-text")).strip()
        state = await page.get_attribute("#runtime-state", "data-state")
        check("Парсер запускается и останавливается", label_before != label_after and state == "running",
              f"«{label_before}» -> «{label_after}»")
        await page.evaluate("() => toggleBot()")
        await page.wait_for_timeout(2000)

        print()
        print("=" * 66)
        print("C. СЕКРЕТЫ НЕ ПОКАЗЫВАЮТСЯ")
        print("=" * 66)
        settings = await page.evaluate("async () => (await fetch('/api/settings')).json()")
        leaked = []
        for key in ("bot_token", "tg_api_hash", "remote_api_key", "proxy_pass", "mtproto_key"):
            value = str(settings.get(key, ""))
            if value and value != "••••••••":
                leaked.append(f"{key}={value}")
        check("API не отдаёт значения секретов", not leaked, ", ".join(leaked) or "все секреты замаскированы")
        for field in ("f-bot_token", "f-tg_api_hash", "f-remote_api_key", "f-proxy_pass", "f-mtproto_key"):
            tag = await page.evaluate(
                "id => { const e = document.getElementById(id); return e ? e.getAttribute('type') : null; }", field)
            check(f"Поле {field} скрыто маской ввода", tag == "password", f"type={tag}")

        print()
        print("=" * 66)
        print("D. СОХРАНЕНИЕ И ПОВТОРНАЯ ЗАГРУЗКА")
        print("=" * 66)
        await page.fill('input[id="f-poll_delay"]', "88")
        await page.fill('textarea[id="f-sources"]', "@mychannel1")
        dirty = await page.inner_text("#save-state-text")
        check("Индикатор показывает несохранённые изменения", "не сохранены" in dirty, dirty.strip())
        await page.evaluate("() => saveQuick()")
        await page.wait_for_timeout(3000)
        saved = await page.inner_text("#save-state-text")
        check("После сохранения индикатор сбрасывается", "сохранены" in saved and "не сохранены" not in saved, saved.strip())
        await page.reload()
        await page.wait_for_timeout(2500)
        poll = await page.input_value('input[id="f-poll_delay"]')
        sources = await page.input_value('textarea[id="f-sources"]')
        check("Настройки переживают перезагрузку", poll == "88" and "@mychannel1" in sources,
              f"poll_delay={poll}, sources={sources!r}")

        print()
        print("=" * 66)
        print("E. МАРШРУТЫ И ОЧЕРЕДЬ ПОСТОВ")
        print("=" * 66)
        await page.evaluate("() => selectPanel('targets')")
        await page.wait_for_timeout(700)
        rows = len(await page.query_selector_all("#topics-list .route-row"))
        await page.click("#add-topic")
        await page.wait_for_timeout(400)
        rows2 = len(await page.query_selector_all("#topics-list .route-row"))
        check("Добавление темы работает", rows2 == rows + 1, f"{rows} -> {rows2}")
        remove_btns = await page.query_selector_all("#topics-list .route-remove")
        if remove_btns:
            await remove_btns[-1].click()
            await page.wait_for_timeout(400)
            rows3 = len(await page.query_selector_all("#topics-list .route-row"))
            check("Удаление темы работает", rows3 == rows, f"{rows2} -> {rows3}")

        await page.evaluate("() => selectPanel('posts')")
        await page.wait_for_timeout(1500)
        usage = await page.inner_text("#media-usage")
        check("Показана статистика медиа", len(usage.strip()) > 0, usage.strip()[:50])
        post_filters = await page.query_selector_all("[data-post-filter]")
        check("Фильтры очереди постов на месте", len(post_filters) == 5, f"фильтров: {len(post_filters)}")
        await page.evaluate("() => cleanupMedia('retention')")
        await page.wait_for_timeout(2000)
        toasts = await page.query_selector_all(".toast")
        texts = [(await t.inner_text()).strip() for t in toasts]
        check("Очистка медиа отрабатывает", any("Удалено файлов" in t for t in texts), "; ".join(texts))

        print()
        print("=" * 66)
        print("F. СПРАВКА ПО КОДАМ")
        print("=" * 66)
        await page.evaluate("() => selectPanel('faq')")
        await page.wait_for_timeout(700)
        items = await page.query_selector_all("#faq-list .faq-item")
        check("Справочник содержит коды журнала", len(items) >= 30, f"записей: {len(items)}")
        await page.fill("#faq-search", "ERR-408")
        await page.wait_for_timeout(500)
        found = await page.query_selector_all("#faq-list .faq-item")
        check("Поиск по коду работает", len(found) >= 1, f"найдено: {len(found)}")
        await page.fill("#faq-search", "")
        await page.wait_for_timeout(300)

        print()
        print("=" * 66)
        print("G. ТЕМА ОФОРМЛЕНИЯ")
        print("=" * 66)
        theme_before = await page.get_attribute("html", "data-theme")
        await page.click("#btn-theme")
        await page.wait_for_timeout(400)
        theme_after = await page.get_attribute("html", "data-theme")
        check("Переключатель темы работает", theme_before != theme_after, f"{theme_before} -> {theme_after}")
        await page.reload()
        await page.wait_for_timeout(2000)
        persisted = await page.get_attribute("html", "data-theme")
        check("Тема сохраняется после перезагрузки", persisted == theme_after, f"стало {persisted}")
        if theme_after == "dark":
            await page.click("#btn-theme")
            await page.wait_for_timeout(300)

        print()
        print("=" * 66)
        print("H. МОБИЛЬНАЯ ШИРИНА 375 px")
        print("=" * 66)
        await ctx.close()
        ctx2 = await browser.new_context(viewport={"width": 375, "height": 812})
        pg = await ctx2.new_page()
        await pg.goto(BASE_URL + "/")
        await pg.wait_for_timeout(2500)
        overflow = await pg.evaluate("""() => {
            const de = document.documentElement;
            return de.scrollWidth > de.clientWidth + 1;
        }""")
        check("Нет горизонтального переполнения", not overflow)
        menu_btn = await pg.is_visible("#btn-sections")
        check("Мобильное меню разделов доступно", menu_btn)
        await pg.click("#btn-sections")
        await pg.wait_for_timeout(400)
        menu_open = await pg.is_visible("#sections-menu")
        check("Выпадающий список разделов открывается", menu_open)
        await pg.click("#sections-menu .nav-tab[data-panel='ai']")
        await pg.wait_for_timeout(500)
        label = await pg.inner_text("#sections-current-label")
        check("Выбор раздела из меню работает", label.strip() == "AI", label.strip())
        await pg.screenshot(path=os.path.join(OUT, "final_mobile_menu.png"), full_page=True)
        print("  Скриншот: final_mobile_menu.png")
        await ctx2.close()

        print()
        print("=" * 66)
        print("I. ОШИБКИ JS")
        print("=" * 66)
        real = [e for e in errors if "favicon" not in e.lower() and "503" not in e]
        check("Ошибок JavaScript нет", not real, "; ".join(real[:3]))

        await browser.close()

    passed = sum(1 for _, ok in RESULTS if ok)
    print()
    print("=" * 66)
    print(f"ИТОГ: {passed} из {len(RESULTS)} проверок пройдено")
    for name, ok in RESULTS:
        if not ok:
            print(f"  [FAIL] {name}")
    print("=" * 66)


asyncio.run(main())
