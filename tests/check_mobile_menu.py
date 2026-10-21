"""Проверка мобильного выпадающего меню разделов."""
from pathlib import Path
import asyncio
import os
import re
import builtins
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8000"
OUT = str(Path(__file__).resolve().parents[1] / "_test_screenshots")

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

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(viewport={"width": 375, "height": 812})
        page = await ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(BASE_URL + "/")
        await page.wait_for_timeout(2200)

        print("=" * 64)
        print("МОБИЛЬНОЕ МЕНЮ РАЗДЕЛОВ (375 px)")
        print("=" * 64)

        # Горизонтальной строки вкладок быть не должно.
        tabs_visible = await page.evaluate("""() => {
            const t = document.querySelector('.nav-tab');
            return t ? t.getBoundingClientRect().width > 0 && t.getBoundingClientRect().left >= 0 : false;
        }""")
        check("Вкладки не вылезают за границу экрана (строка скрыта)", not tabs_visible)
        btn_visible = await page.is_visible("#btn-sections")
        check("Показан выпадающий переключатель раздела", btn_visible)

        label = await page.inner_text("#sections-current-label")
        check("Переключатель показывает текущий раздел", label.strip() == "Подключение", label.strip())

        # Открытие меню.
        await page.click("#btn-sections")
        await page.wait_for_timeout(400)
        menu_open = await page.is_visible("#sections-menu")
        check("Меню открывается по нажатию", menu_open)
        items = await page.query_selector_all("#sections-menu .nav-tab")
        nav_tabs = await page.query_selector_all(".nav-tab[data-panel]")
        check("В меню перечислены все разделы", len(items) == len(nav_tabs) and len(items) >= 6,
              f"пунктов в меню: {len(items)}, вкладок в навигации: {len(nav_tabs)}")
        expanded = await page.get_attribute("#btn-sections", "aria-expanded")
        check("Состояние меню отражено в aria-expanded", expanded == "true", f"aria-expanded={expanded}")
        await page.screenshot(path=os.path.join(OUT, "mob_01_menu_open.png"))
        print("  Скриншот: mob_01_menu_open.png")

        # Выбор раздела из меню.
        await page.click("#sections-menu .nav-tab[data-panel='ai']")
        await page.wait_for_timeout(500)
        menu_closed = not await page.is_visible("#sections-menu")
        check("После выбора раздела меню закрывается", menu_closed)
        new_label = await page.inner_text("#sections-current-label")
        check("Название раздела обновилось", new_label.strip() == "AI", new_label.strip())
        panel_visible = await page.evaluate("""() => {
            const e = document.querySelector('.panel[data-panel="ai"]');
            return e ? e.classList.contains('active') && e.offsetHeight > 0 : false;
        }""")
        check("Выбранный раздел открылся", panel_visible)
        await page.screenshot(path=os.path.join(OUT, "mob_02_ai_selected.png"))
        print("  Скриншот: mob_02_ai_selected.png")

        # Закрытие кликом вне меню.
        await page.click("#btn-sections")
        await page.wait_for_timeout(300)
        await page.locator(".page-title:visible").first.click()
        await page.wait_for_timeout(400)
        check("Меню закрывается кликом вне", not await page.is_visible("#sections-menu"))

        # Закрытие по Escape.
        await page.click("#btn-sections")
        await page.wait_for_timeout(300)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        check("Меню закрывается по Escape", not await page.is_visible("#sections-menu"))

        # Переполнение по горизонтали.
        overflow = await page.evaluate("""() => {
            const de = document.documentElement;
            return de.scrollWidth > de.clientWidth + 1;
        }""")
        check("Горизонтального переполнения нет", not overflow)

        # Десктоп: горизонтальная строка, кнопка скрыта.
        await ctx.close()
        ctx2 = await browser.new_context(viewport={"width": 1600, "height": 1000})
        pg = await ctx2.new_page()
        await pg.goto(BASE_URL + "/")
        await pg.wait_for_timeout(2200)
        desk_tabs = await pg.is_visible(".nav-tab")
        desk_btn = await pg.is_visible("#btn-sections")
        check("На десктопе видна строка разделов", desk_tabs)
        check("На десктопе выпадающий переключатель скрыт", not desk_btn)
        await pg.screenshot(path=os.path.join(OUT, "mob_03_desktop_tabs.png"))
        print("  Скриншот: mob_03_desktop_tabs.png")

        await ctx2.close()
        await browser.close()

    passed = sum(1 for _, ok in RESULTS if ok)
    print()
    print(f"ИТОГ: {passed} из {len(RESULTS)}")
    for name, ok in RESULTS:
        if not ok:
            print(f"  [FAIL] {name}")


asyncio.run(main())
