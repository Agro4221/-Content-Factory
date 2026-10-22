"use strict";

// Проверка разбора и сбора маршрутов «канал → теги» в админ-панели
// Content Factory. Панель самодостаточна, поэтому логика извлекается
// из admin.html.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "admin.html"), "utf8");
// Основная логика панели — в самом крупном блоке <script>.
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

// Строка маршрута в панели: канал и теги.
function makeRow(chat, tags) {
    return {
        querySelector(selector) {
            if (selector === ".topic-name") return { value: tags };
            if (selector === ".topic-chat") return { value: chat };
            return null;
        },
    };
}

// Разбор сохранённого JSON с маршрутами.
{
    const sandbox = { JSON };
    vm.createContext(sandbox);
    vm.runInContext(
        extractFunction("parseTopics") + "\nthis.parseTopics = parseTopics;",
        sandbox,
        { timeout: 1000 }
    );
    const parse = sandbox.parseTopics;

    assert.deepEqual(
        JSON.parse(JSON.stringify(parse('{"@target":["news","urgent"]}'))),
        { "@target": ["news", "urgent"] },
        "valid tag routes should be parsed"
    );
    for (const malformed of ["null", "[]", "42", "true", '"text"', "not-json", ""]) {
        assert.deepEqual(
            JSON.parse(JSON.stringify(parse(malformed))),
            {},
            `malformed tag routes should safely become an empty object: ${malformed}`
        );
    }
}

// Сбор маршрутов из строк формы: неполные и пустые строки игнорируются,
// теги приводятся к нижнему регистру и делятся по запятой.
{
    const rows = [
        makeRow("@tech_channel", "Tech, urgent"),
        makeRow("@news_channel", "news"),
        makeRow("@orphan_channel", ""),
        makeRow("", "nochat"),
        makeRow("@blank_channel", "   "),
    ];
    const sandbox = {
        JSON,
        document: {
            querySelectorAll(selector) {
                return selector === "#topics-list .route-row" ? rows : [];
            },
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(
        extractFunction("collectTopics") + "\nthis.collectTopics = collectTopics;",
        sandbox,
        { timeout: 1000 }
    );
    const collected = JSON.parse(JSON.stringify(sandbox.collectTopics()));
    assert.deepEqual(collected, {
        "@tech_channel": ["tech", "urgent"],
        "@news_channel": ["news"],
    });
}

// Один канал с несколькими тегами: теги не теряются.
{
    const rows = [
        makeRow("@tech_channel", "tech"),
        makeRow("@tech_channel", "urgent, science"),
    ];
    const sandbox = {
        JSON,
        document: {
            querySelectorAll(selector) {
                return selector === "#topics-list .route-row" ? rows : [];
            },
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(
        extractFunction("collectTopics") + "\nthis.collectTopics = collectTopics;",
        sandbox,
        { timeout: 1000 }
    );
    const collected = JSON.parse(JSON.stringify(sandbox.collectTopics()));
    assert.deepEqual(collected, {
        "@tech_channel": ["tech", "urgent", "science"],
    });
}

// Панель не должна вшивать значения маршрутов в разметку.
{
    assert.ok(!/tag_routes"\s*value=/.test(html), "no tag route value may be embedded in markup");
}

// Маршруты сохраняются в настройку tag_routes, а не в старую topic_channels.
{
    assert.ok(
        /payload\['tag_routes'\]\s*=\s*JSON\.stringify\(collectTopics\(\)\)/.test(script),
        "routes must be saved into the tag_routes setting"
    );
}

console.log("Tag-route UI regression tests passed (13 cases).");
