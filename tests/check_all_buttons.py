"""Функциональная проверка: каждая кнопка и переключатель кликаются
и дают наблюдаемый результат.
"""
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
    RESULTS.append((name, ok, detail))
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))


async def main():
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
        print("A. ШАПКА")
        print("=" * 66)

        # Кнопка темы.
        theme_before = await page.get_attribute("html", "data-theme")
        await page.click("#btn-theme")
        await page.wait_for_timeout(400)
        theme_after = await page.get_attribute("html", "data-theme")
        check("Переключатель темы меняет оформление", theme_before != theme_after,
              f"{theme_before} -> {theme_after}")
        stored = await page.evaluate("() => localStorage.getItem('content-factory-theme')")
        check("Выбор темы сохраняется в браузере", stored == theme_after, f"localStorage={stored}")
        # Возвращаем обратно и проверяем, что выбор переживает перезагрузку.
        await page.click("#btn-theme")
        await page.wait_for_timeout(300)
        await page.reload()
        await page.wait_for_timeout(2000)
        theme_reloaded = await page.get_attribute("html", "data-theme")
        check("Тема сохраняется после перезагрузки", theme_reloaded == theme_before,
              f"стало {theme_reloaded}")

        # Кнопка обновления.
        await page.evaluate("() => { window.__refreshCalled = 0; const orig = window.refreshAll; window.refreshAll = function(){ window.__refreshCalled++; return orig.apply(this, arguments); }; }")
        await page.click("#btn-refresh")
        await page.wait_for_timeout(1200)
        calls = await page.evaluate("() => window.__refreshCalled")
        check("Кнопка «Обновить» вызывает перезагрузку состояния", calls >= 1, f"вызовов: {calls}")

        # Кнопка запуска парсера.
        label_before = await page.inner_text("#btn-bot-text")
        await page.click("#btn-bot-toggle")
        await page.wait_for_timeout(2500)
        label_after = await page.inner_text("#btn-bot-text")
        state_after = await page.get_attribute("#runtime-state", "data-state")
        check("Кнопка запуска/остановки меняет состояние",
              label_before != label_after and state_after == "running",
              f"«{label_before.strip()}» -> «{label_after.strip()}», state={state_after}")
        await page.click("#btn-bot-toggle")
        await page.wait_for_timeout(2000)
        state_back = await page.get_attribute("#runtime-state", "data-state")
        check("Парсер останавливается обратно", state_back == "stopped", f"state={state_back}")

        print()
        print("=" * 66)
        print("B. НАВИГАЦИЯ ПО ВКЛАДКАМ")
        print("=" * 66)
        tabs = await page.query_selector_all(".nav-tab")
        for i, tab in enumerate(tabs):
            panel = await tab.get_attribute("data-panel")
            await tab.click()
            await page.wait_for_timeout(400)
            active = await tab.evaluate("el => el.classList.contains('active')")
            visible = await page.evaluate(
                "p => { const e = document.querySelector('.panel[data-panel=\"' + p + '\"]');"
                " return e ? e.classList.contains('active') && e.offsetHeight > 0 : false; }", panel)
            check(f"Вкладка «{panel}» переключает раздел", active and visible)

        print()
        print("=" * 66)
        print("C. ПРОКСИ: ПЕРЕКЛЮЧАТЕЛЬ")
        print("=" * 66)
        await page.evaluate("() => selectPanel('proxy')")
        await page.wait_for_timeout(500)
        fields_before = await page.is_visible("#proxy-fields")
        await page.click(".switch-row[for='f-use_proxy']")
        await page.wait_for_timeout(500)
        fields_after = await page.is_visible("#proxy-fields")
        checked = await page.is_checked("#f-use_proxy")
        check("Переключатель прокси открывает параметры подключения",
              (not fields_before) and fields_after and checked,
              f"было {fields_before} -> стало {fields_after}, checked={checked}")
        # Проверяем, что состояние не откатывается автообновлением.
        await page.wait_for_timeout(16000)
        still_checked = await page.is_checked("#f-use_proxy")
        still_visible = await page.is_visible("#proxy-fields")
        check("Состояние переключателя не откатывается автообновлением",
              still_checked and still_visible,
              f"checked={still_checked}, поля видны={still_visible}")
        await page.click(".switch-row[for='f-use_proxy']")
        await page.wait_for_timeout(400)

        print()
        print("=" * 66)
        print("D. AI: ПЕРЕКЛЮЧЕНИЕ РЕЖИМА")
        print("=" * 66)
        await page.evaluate("() => selectPanel('ai')")
        await page.wait_for_timeout(500)
        # Ожидаемый режим берём из сохранённых настроек, а не из предположения.
        saved_mode = await page.evaluate("""async () => {
            const r = await fetch('/api/settings');
            const s = await r.json();
            return String(s.ai_mode || 'LOCAL').toUpperCase();
        }""")
        local_visible = await page.is_visible("#ai-local-fields")
        remote_visible = await page.is_visible("#ai-remote-fields")
        expected_local = saved_mode != "REMOTE"
        check("Панель AI показывает сохранённый режим",
              local_visible == expected_local and remote_visible == (not expected_local),
              f"в БД {saved_mode}, локальный виден={local_visible}, облако видно={remote_visible}")
        await page.click("label:has(#ai_source_remote)")
        await page.wait_for_timeout(500)
        remote_visible = await page.is_visible("#ai-remote-fields")
        local_hidden = not await page.is_visible("#ai-local-fields")
        mode_checked = await page.is_checked("#f-ai_mode")
        check("Переключение на облако меняет набор полей",
              remote_visible and local_hidden and mode_checked,
              f"облако видно={remote_visible}, локально скрыто={local_hidden}")
        await page.click("label:has(#ai_source_local)")
        await page.wait_for_timeout(500)
        back_local = await page.is_visible("#ai-local-fields")
        check("Возврат к локальному режиму работает", back_local)

        print()
        print("=" * 66)
        print("E. СЕКРЕТЫ: ПОКАЗАТЬ/СКРЫТЬ И УДАЛИТЬ")
        print("=" * 66)
        await page.evaluate("() => selectPanel('quick')")
        await page.wait_for_timeout(500)
        type_before = await page.get_attribute("#f-bot_token", "type")
        await page.click("[data-secret-toggle='f-bot_token']")
        await page.wait_for_timeout(300)
        type_after = await page.get_attribute("#f-bot_token", "type")
        check("Кнопка «глаз» показывает/скрывает секрет",
              type_before == "password" and type_after == "text",
              f"{type_before} -> {type_after}")
        await page.click("[data-secret-toggle='f-bot_token']")
        await page.wait_for_timeout(300)
        type_back = await page.get_attribute("#f-bot_token", "type")
        check("Повторное нажатие снова скрывает секрет", type_back == "password")

        clear_btn = await page.is_visible("[data-secret-clear='f-bot_token']")
        check("Кнопка удаления секрета скрыта, пока секрет не задан", not clear_btn)

        print()
        print("=" * 66)
        print("F. МАРШРУТЫ: ДОБАВИТЬ И УДАЛИТЬ")
        print("=" * 66)
        await page.evaluate("() => selectPanel('targets')")
        await page.wait_for_timeout(500)
        rows_before = len(await page.query_selector_all("#topics-list .route-row"))
        await page.click("#add-topic")
        await page.wait_for_timeout(400)
        rows_after = len(await page.query_selector_all("#topics-list .route-row"))
        check("Кнопка «Добавить тему» добавляет строку", rows_after == rows_before + 1,
              f"{rows_before} -> {rows_after}")
        await page.click("#topics-list .route-row:last-child .route-remove")
        await page.wait_for_timeout(400)
        rows_final = len(await page.query_selector_all("#topics-list .route-row"))
        check("Кнопка удаления маршрута убирает строку", rows_final == rows_before,
              f"{rows_after} -> {rows_final}")

        print()
        print("=" * 66)
        print("G. ЖУРНАЛ: ОБНОВИТЬ, УДАЛИТЬ, ПОДТВЕРЖДЕНИЕ")
        print("=" * 66)
        await page.evaluate("() => selectPanel('logs')")
        await page.wait_for_timeout(500)
        errors_before_logs = len(errors)
        await page.click("#acc-logs button:has-text('Обновить')")
        await page.wait_for_timeout(1200)
        new_errors = errors[errors_before_logs:]
        check("Кнопка «Обновить» в журнале не вызывает ошибок", not new_errors,
              "; ".join(new_errors[:3]))
        if errors:
            print("  Накопленные ошибки консоли за весь прогон:")
            for e in errors:
                print("   -", _safe(e))

        await page.click("#clear-logs-btn")
        await page.wait_for_timeout(400)
        confirm_shown = await page.is_visible("#clear-logs-confirm")
        check("Кнопка «Удалить записи» запрашивает подтверждение", confirm_shown)
        await page.click("button:has-text('Отмена')")
        await page.wait_for_timeout(400)
        confirm_hidden = not await page.is_visible("#clear-logs-confirm")
        check("Кнопка «Отмена» скрывает подтверждение", confirm_hidden)

        print()
        print("=" * 66)
        print("H. СОХРАНЕНИЕ")
        print("=" * 66)
        await page.evaluate("() => selectPanel('quick')")
        await page.wait_for_timeout(500)
        await page.fill('input[id="f-poll_delay"]', "90")
        dirty = await page.inner_text("#save-state-text")
        check("Индикатор показывает несохранённые изменения", "не сохранены" in dirty, dirty)
        await page.click("#btn-save")
        await page.wait_for_timeout(3000)
        saved_state = await page.inner_text("#save-state-text")
        check("После сохранения индикатор сбрасывается", "сохранены" in saved_state and "не сохранены" not in saved_state, saved_state)
        await page.reload()
        await page.wait_for_timeout(2500)
        reloaded = await page.input_value('input[id="f-poll_delay"]')
        check("Изменение сохранилось на сервере", reloaded == "90", f"poll_delay={reloaded}")

        print()
        print("=" * 66)
        print("I. ОШИБКИ JS")
        print("=" * 66)
        real = [e for e in errors if "favicon" not in e.lower()]
        check("Ошибок JavaScript нет", not real, "; ".join(real[:3]))

        await page.screenshot(path=os.path.join(OUT, "fn_01_final.png"), full_page=True)
        print("Скриншот: fn_01_final.png")

        await ctx.close()
        await browser.close()

    print()
    print("=" * 66)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"ИТОГ: {passed} из {len(RESULTS)} проверок пройдено")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  [FAIL] {name}  — {detail}")
    print("=" * 66)


asyncio.run(main())
