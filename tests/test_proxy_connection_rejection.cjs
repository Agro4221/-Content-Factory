"use strict";

// Проверка поведения проверки прокси в админ-панели Content Factory.
// Панель самодостаточна, поэтому логика извлекается из admin.html.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "admin.html"), "utf8");
// В документе несколько блоков <script> (раннее применение темы + основная
// логика). Основная логика — в самом крупном блоке.
const scriptBlocks = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
assert.ok(scriptBlocks.length > 0, "inline script should exist in admin.html");
const script = scriptBlocks.reduce((a, b) => (b.length > a.length ? b : a));

function extractFunction(name) {
    const escaped = name.replace(/[.*+?^$()|[\]\\]/g, "\\$&");
    const pattern = new RegExp("(?:async\\s+)?function\\s+" + escaped + "\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\}");
    const match = script.match(pattern);
    assert.ok(match, "function " + name + " should exist");
    return match[0];
}

function makeElement(id) {
    return {
        id,
        value: "",
        textContent: "",
        innerHTML: "",
        type: "text",
        checked: false,
        dataset: {},
        style: {},
        options: [],
        classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
        appendChild() {},
        remove() {},
        setAttribute() {},
        getAttribute() { return null; },
        addEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
}

// Панель хранит сохранённые секреты в маске и не отправляет маску на сервер.
function makePanelElements(overrides = {}) {
    const elements = {
        "f-use_proxy": Object.assign(makeElement("f-use_proxy"), { checked: true }),
        "f-proxy_type": Object.assign(makeElement("f-proxy_type"), { value: "http" }),
        "f-proxy_host": Object.assign(makeElement("f-proxy_host"), { value: "proxy.example" }),
        "f-proxy_port": Object.assign(makeElement("f-proxy_port"), { value: "8080" }),
        "f-proxy_user": Object.assign(makeElement("f-proxy_user"), { value: "" }),
        "f-proxy_pass": Object.assign(makeElement("f-proxy_pass"), { value: "" }),
        "f-mtproto_key": Object.assign(makeElement("f-mtproto_key"), { value: "" }),
        "proxy-result": makeElement("proxy-result"),
    };
    return Object.assign(elements, overrides);
}

async function runProxyCheck(elements, fetchImpl) {
    const sandbox = {
        document: {
            getElementById(id) { return elements[id] || null; },
            createElement() { return makeElement("created"); },
        },
        $id(id) { return elements[id] || null; },
        API: "",
        escapeHtml(value) {
            return String(value === null || value === undefined ? "" : value)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;");
        },
        fetch: fetchImpl,
        JSON,
        console,
    };
    vm.createContext(sandbox);
    const source = extractFunction("testProxy");
    vm.runInContext(source + "\nthis.__testProxy = testProxy;", sandbox, { timeout: 1000 });
    await sandbox.__testProxy();
    const box = elements["proxy-result"];
    return { html: box.innerHTML, className: box.className };
}

async function main() {
    // 1. Сетевая ошибка: результат не показывает, что прокси доступен.
    {
        let requestUrl = "";
        const result = await runProxyCheck(makePanelElements(), async (url) => {
            requestUrl = url;
            throw new TypeError("Failed to fetch");
        });
        assert.equal(requestUrl, "/api/proxy/test");
        assert.ok(result.className.includes("err"), "network failure must be shown as a failure");
        assert.ok(!result.className.includes("ok"), "network failure must not be shown as success");
    }

    // 2. Ошибка сервера: сообщение бэкенда показывается пользователю.
    {
        const result = await runProxyCheck(makePanelElements(), async () => ({
            ok: false,
            status: 502,
            json: async () => ({ message: "Сервер прокси недоступен" }),
        }));
        assert.ok(result.className.includes("err"), "server failure must be shown as a failure");
        assert.ok(
            result.html.includes("Сервер прокси недоступен"),
            "backend message must reach the user",
        );
    }

    // 3. Успех: показывается отметка о доступности.
    {
        const result = await runProxyCheck(makePanelElements(), async () => ({
            ok: true,
            status: 200,
            json: async () => ({ ok: true, reachable: true, state: "reachable" }),
        }));
        assert.ok(result.className.includes("ok"), "successful check must be shown as success");
        assert.ok(result.html.includes("#i-check-circle"), "success must use the check icon");
    }

    // 4. Секретные поля отправляются на сервер как есть, без маски.
    {
        let body = null;
        const elements = makePanelElements({
            "f-proxy_pass": Object.assign(makeElement("f-proxy_pass"), { value: "p@ssw0rd" }),
        });
        await runProxyCheck(elements, async (url, options) => {
            body = JSON.parse(options.body);
            return { ok: true, status: 200, json: async () => ({ ok: true, reachable: true, state: "reachable" }) };
        });
        assert.equal(body.proxy_pass, "p@ssw0rd");
        assert.equal(body.proxy_host, "proxy.example");
        assert.equal(body.proxy_port, "8080");
    }

    console.log("Proxy connection UI regression tests passed (4 cases).");
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
