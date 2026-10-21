"use strict";

// Проверки безопасности запуска админ-панели Content Factory.
// Панель самодостаточна: логика лежит внутри admin.html, поэтому тест
// извлекает встроенный <script> и выполняет его в изолированном контексте.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "admin.html"), "utf8");

function extractBlock(tag) {
    // В документе несколько блоков: раннее применение темы и основная логика.
    // Для проверки нужен самый крупный блок нужного типа.
    const blocks = [...html.matchAll(new RegExp("<" + tag + "\\b[^>]*>([\\s\\S]*?)</" + tag + ">", "gi"))]
        .map(m => m[1]);
    assert.ok(blocks.length > 0, tag + " block should exist in admin.html");
    return blocks.reduce((a, b) => (b.length > a.length ? b : a));
}

const script = extractBlock("script");
const css = extractBlock("style");

function makeElement(id) {
    const classes = new Set();
    const children = [];
    return {
        id,
        value: "",
        textContent: "",
        type: "text",
        checked: false,
        disabled: false,
        hidden: false,
        placeholder: "",
        dataset: {},
        style: {},
        options: [],
        classList: {
            add(name) { classes.add(name); },
            remove(name) { classes.delete(name); },
            contains(name) { return classes.has(name); },
            toggle(name, enabled) {
                if (enabled === undefined) {
                    if (classes.has(name)) { classes.delete(name); return false; }
                    classes.add(name); return true;
                }
                if (enabled) classes.add(name); else classes.delete(name);
                return enabled;
            },
        },
        appendChild(child) { children.push(child); return child; },
        removeChild(child) { const i = children.indexOf(child); if (i >= 0) children.splice(i, 1); },
        remove() {},
        setAttribute() {},
        getAttribute() { return null; },
        addEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        focus() {},
        click() {},
        insertBefore(child) { children.push(child); return child; },
    };
}

// Минимальный DOM: панель должна загружаться без исключений.
function inertDocument(elements) {
    return {
        documentElement: { dataset: {} },
        body: makeElement("body"),
        getElementById(id) {
            if (!elements[id]) elements[id] = makeElement(id);
            return elements[id];
        },
        querySelectorAll() { return []; },
        querySelector() { return null; },
        createElement(tag) { return makeElement(tag); },
        addEventListener() {},
        removeEventListener() {},
    };
}

// 1. Скрипт панели загружается без ReferenceError и прочих исключений.
{
    const elements = {};
    const sandbox = {
        document: inertDocument(elements),
        window: { location: { hash: "" }, scrollTo() {} },
        localStorage: { getItem() { return null; }, setItem() {} },
        setInterval() { return 0; },
        clearInterval() {},
        setTimeout() { return 0; },
        clearTimeout() {},
        console,
        JSON,
        Math,
        Date,
        Set,
        Map,
        RegExp,
        Number,
        String,
        Boolean,
        Array,
        Object,
        Error,
        Promise,
        fetch: async () => ({
            ok: true,
            status: 200,
            json: async () => ({ modules: [], summary: {}, proxy_type: "", message: "" }),
            text: async () => "",
        }),
    };
    sandbox.window = sandbox;
    sandbox.window.location = { hash: "" };
    sandbox.window.scrollTo = () => {};
    vm.createContext(sandbox);
    assert.doesNotThrow(() => {
        vm.runInContext(script, sandbox, { timeout: 2000 });
    }, "panel script must load without throwing");
}

// 2. Каждый id, к которому обращается скрипт, присутствует в разметке.
{
    const referenced = new Set();
    for (const m of script.matchAll(/getElementById\(['"]([^'"]+)['"]\)/g)) {
        referenced.add(m[1]);
    }
    for (const m of script.matchAll(/\$\(['"]([^'"]+)['"]\)/g)) {
        referenced.add(m[1]);
    }
    const present = new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]));
    const missing = [...referenced].filter(id => !present.has(id)).sort();
    assert.deepEqual(missing, [], "script must not reference missing elements");
}

// 3. Кнопка запуска парсера обрабатывается ровно одним обработчиком.
//    Два обработчика означали бы, что нажатие стартует и останавливает
//    парсер одновременно.
{
    const button = html.match(/<button\b[^>]*id="btn-bot-toggle"[^>]*>/);
    assert.ok(button, "start/stop button should exist");
    const inline = button[0].match(/onclick\s*=/g) || [];
    assert.ok(inline.length <= 1, "start/stop button must have at most one inline handler");
    // Обработчик в разметке и в скрипте одновременно — двойное срабатывание.
    const scriptWired = /btn-bot-toggle[\s\S]{0,120}?addEventListener/.test(script)
        || /addEventListener[\s\S]{0,120}?btn-bot-toggle/.test(script);
    assert.ok(
        !(inline.length === 1 && scriptWired),
        "start/stop button must not be wired twice",
    );
    assert.ok(
        script.includes("toggleBot"),
        "start/stop action must be implemented in the script",
    );
}

// 4. Секретные поля не получают значения из разметки.
{
    const secretInputs = [...html.matchAll(/<input\b[^>]*id="(f-[^"]*(?:token|key|pass)[^"]*)"[^>]*>/gi)];
    assert.ok(secretInputs.length >= 4, "secret inputs should be present");
    for (const match of secretInputs) {
        assert.ok(
            !/value="/.test(match[0]),
            "secret input must not carry a value attribute: " + match[1],
        );
        assert.ok(
            /type="password"/.test(match[0]),
            "secret input must be masked: " + match[1],
        );
    }
    // В разметке не должно быть значений секретов.
    assert.ok(!/bot_token"\s*value=/.test(html), "no secret value may be embedded in markup");
}

// 5. Навигация: каждая вкладка соответствует своей панели, стили на месте.
{
    const tabs = [...html.matchAll(/class="nav-tab"[^>]*data-panel="([^"]+)"/g)].map(m => m[1]);
    const panels = [...html.matchAll(/class="panel"[^>]*data-panel="([^"]+)"/g)].map(m => m[1]);
    assert.ok(tabs.length >= 5, "panel should have several navigation tabs");
    assert.equal(tabs.length, panels.length, "each tab must have a matching panel");
    for (const tab of tabs) {
        assert.ok(panels.includes(tab), "tab " + tab + " must have a matching panel");
    }
    assert.ok(css.includes(".panel.active"), "active panel must be visible");
    assert.ok(css.includes(".nav-tab.active"), "active tab must be highlighted");
    assert.ok(css.includes(".status-strip"), "status strip must be styled");
    assert.ok(css.includes(".action-bar"), "action bar must be styled");
}

// 6. Адаптивность: узкий экран перестраивает сетку и маршруты.
{
    assert.ok(
        /@media\s*\(max-width:\s*620px\)/.test(css),
        "narrow-screen breakpoint must exist",
    );
    assert.ok(
        /\.route-row\s*\{[^}]*grid-template-columns/.test(css),
        "route rows must use a grid that can collapse",
    );
    assert.ok(
        /@media\s*\(max-width:\s*620px\)[\s\S]*?\.route-row\s*\{/.test(css),
        "route rows must collapse on narrow screens",
    );
}

// 7. Подтверждение очистки журнала присутствует в разметке.
{
    assert.ok(
        html.includes('id="clear-logs-confirm"'),
        "log clearing must ask for confirmation",
    );
    assert.ok(
        script.includes("confirmClearLogs"),
        "log clearing must go through a confirmed action",
    );
    assert.ok(
        script.includes("/api/logs/clear"),
        "log clearing must use the protected endpoint",
    );
}

console.log("Admin startup/navigation regression tests passed (7 groups).");
